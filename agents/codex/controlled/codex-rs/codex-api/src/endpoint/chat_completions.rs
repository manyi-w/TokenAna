//! Native Chat Completions transport for the TokenAna compatibility build.
//! It shares provider auth/retry transport but never translates through a proxy.
use crate::auth::SharedAuthProvider;
use crate::common::{ResponseEvent, ResponseStream, ResponsesApiRequest};
use crate::endpoint::session::EndpointSession;
use crate::error::ApiError;
use crate::provider::Provider;
use crate::endpoint::responses::ResponsesOptions;
use codex_client::{EncodedJsonBody, HttpTransport, RequestTelemetry};
use codex_protocol::models::ResponseItem;
use codex_protocol::protocol::TokenUsage;
use eventsource_stream::Eventsource;
use futures::StreamExt;
use http::{HeaderValue, Method};
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;
use tokio::sync::mpsc;

pub struct ChatCompletionsClient<T: HttpTransport> { session: EndpointSession<T> }

impl<T: HttpTransport> ChatCompletionsClient<T> {
    pub fn new(transport: T, provider: Provider, auth: SharedAuthProvider) -> Self {
        Self { session: EndpointSession::new(transport, provider, auth) }
    }

    pub fn with_telemetry(mut self, request: Option<Arc<dyn RequestTelemetry>>) -> Self {
        self.session = self.session.with_request_telemetry(request);
        self
    }

    pub async fn stream_request(&self, request: ResponsesApiRequest, options: ResponsesOptions) -> Result<ResponseStream, ApiError> {
        let value = serde_json::to_value(request).map_err(invalid)?;
        let (body, custom, namespaces) = chat_request(value)?;
        let body = EncodedJsonBody::encode(&body).map_err(invalid)?;
        let response = self.session.stream_encoded_json_with(Method::POST, "/chat/completions",
            options.extra_headers, Some(body), |req| {
                req.headers.insert(http::header::ACCEPT, HeaderValue::from_static("text/event-stream"));
            }).await?;
        let upstream_request_id = response.headers.get("x-request-id").and_then(|v| v.to_str().ok()).map(str::to_owned);
        let is_sse = response.headers.get("content-type").and_then(|v| v.to_str().ok()).is_some_and(|v| v.contains("text/event-stream"));
        let idle = self.session.provider().stream_idle_timeout;
        let (tx, rx_event) = mpsc::channel(1600);
        tokio::spawn(async move {
            let result: Result<(), ApiError> = async {
                let mut state = ChatState::default();
                tx.send(Ok(ResponseEvent::Created { response_id: None })).await.map_err(invalid)?;
                if is_sse {
                    let mut stream = response.bytes.eventsource();
                    let mut done = false;
                    while let Some(event) = tokio::time::timeout(idle, stream.next()).await.map_err(invalid)? {
                        let event = event.map_err(invalid)?;
                        if event.data.trim() == "[DONE]" { done = true; break; }
                        let chunk: Value = serde_json::from_str(&event.data).map_err(invalid)?;
                        state.add(&chunk, &tx).await?;
                    }
                    if !done { return Err(invalid("Chat stream ended without [DONE]")); }
                } else {
                    let mut bytes = response.bytes;
                    let mut body = Vec::new();
                    while let Some(chunk) = tokio::time::timeout(idle, bytes.next()).await.map_err(invalid)? {
                        body.extend_from_slice(&chunk.map_err(invalid)?);
                    }
                    state.add(&serde_json::from_slice::<Value>(&body).map_err(invalid)?, &tx).await?;
                }
                state.finish(&tx, &custom, &namespaces).await
            }.await;
            if let Err(error) = result { let _ = tx.send(Err(error)).await; }
        });
        Ok(ResponseStream { rx_event, upstream_request_id })
    }
}

fn invalid(error: impl std::fmt::Display) -> ApiError { ApiError::Stream(format!("Chat Completions: {error}")) }

type ToolNamespaces = BTreeMap<String, (String, String)>;

fn chat_request(value: Value) -> Result<(Value, HashSet<String>, ToolNamespaces), ApiError> {
    let mut namespaces = ToolNamespaces::new();
    let mut definitions = Vec::new();
    for tool in value["tools"].as_array().map(Vec::as_slice).unwrap_or(&[]) {
        if tool["type"] == "namespace" {
            let namespace = tool["name"].as_str().ok_or_else(|| invalid("missing namespace"))?;
            for child in tool["tools"].as_array().ok_or_else(|| invalid("missing namespace tools"))? {
                let name = child["name"].as_str().ok_or_else(|| invalid("missing tool name"))?;
                let alias = format!("{namespace}__{name}");
                if namespaces.insert(alias.clone(), (namespace.to_owned(), name.to_owned())).is_some() {
                    return Err(invalid("duplicate tool alias"));
                }
                let mut definition = child.clone();
                definition["name"] = json!(alias);
                definitions.push(definition);
            }
        } else { definitions.push(tool.clone()); }
    }
    let mut messages = vec![json!({"role":"system", "content":value["instructions"].as_str().unwrap_or("")})];
    for item in value["input"].as_array().ok_or_else(|| invalid("missing input"))? {
        match item["type"].as_str().unwrap_or("message") {
            "message" => {
                let role = item["role"].as_str().ok_or_else(|| invalid("missing role"))?;
                let mut content = Vec::new();
                for block in item["content"].as_array().ok_or_else(|| invalid("missing content"))? {
                    match block["type"].as_str() {
                        Some("input_text" | "output_text") => content.push(json!({"type":"text", "text":block["text"]})),
                        Some("input_image") => content.push(json!({"type":"image_url", "image_url":{"url":block["image_url"]}})),
                        _ => return Err(invalid("unsupported content block")),
                    }
                }
                messages.push(json!({"role":role, "content":content}));
            }
            "function_call" | "custom_tool_call" => {
                let arguments = if item["type"] == "custom_tool_call" {
                    json!({"input":item["input"]}).to_string()
                } else { item["arguments"].as_str().ok_or_else(|| invalid("missing tool arguments"))?.to_owned() };
                let name = item["name"].as_str().ok_or_else(|| invalid("missing function name"))?;
                let wire_name = item["namespace"].as_str().map(|ns| format!("{ns}__{name}")).unwrap_or_else(|| name.to_owned());
                let tool = json!({"id":item["call_id"],"type":"function", "function":{"name":wire_name,"arguments":arguments}});
                if let Some(previous) = messages.last_mut().filter(|m| m["role"] == "assistant" && m.get("tool_calls").is_some()) {
                    previous["tool_calls"].as_array_mut().ok_or_else(|| invalid("invalid tools"))?.push(tool);
                } else { messages.push(json!({"role":"assistant","content":null,"tool_calls":[tool]})); }
            }
            "function_call_output" | "custom_tool_call_output" => {
                let content = if item["output"].is_string() { item["output"].clone() } else {
                    let blocks = item["output"].as_array().ok_or_else(|| invalid("unsupported tool output"))?;
                    let mut text = String::new();
                    for block in blocks {
                        if !matches!(block["type"].as_str(), Some("input_text" | "text")) { return Err(invalid("nontext tool output unsupported")); }
                        text.push_str(block["text"].as_str().ok_or_else(|| invalid("missing tool text"))?);
                    }
                    json!(text)
                };
                messages.push(json!({"role":"tool", "tool_call_id":item["call_id"], "content":content}));
            }
            // Opaque reasoning is retained in native history, not fabricated as chat text.
            "reasoning" => {},
            other => return Err(invalid(format!("unsupported input item {other}"))),
        }
    }
    let mut custom = HashSet::new();
    let mut tools = Vec::new();
    let mut names = HashSet::new();
    for tool in &definitions {
        if !names.insert(tool["name"].as_str().unwrap_or("").to_owned()) { return Err(invalid("duplicate Chat tool name")); }
        let function = match tool["type"].as_str() {
            Some("function") => json!({"name":tool["name"], "description":tool["description"], "parameters":tool["parameters"]}),
            Some("custom") => {
                let name = tool["name"].as_str().ok_or_else(|| invalid("missing custom tool name"))?;
                custom.insert(name.to_owned());
                json!({"name":name,"description":tool["description"],"parameters":{"type":"object","properties":{"input":{"type":"string"}},"required":["input"],"additionalProperties":false}})
            }
            _ => return Err(invalid("unsupported Chat tool schema")),
        };
        tools.push(json!({"type":"function","function":function}));
    }
    let mut body = json!({"model":value["model"], "messages":messages, "stream":true,
        "stream_options":{"include_usage":true}, "tools":tools, "parallel_tool_calls":value["parallel_tool_calls"]});
    if let Some(max) = value.get("max_output_tokens") { body["max_tokens"] = max.clone(); }
    Ok((body, custom, namespaces))
}

#[derive(Default)]
struct ChatState { id: String, text: String, calls: BTreeMap<u64, (String, String, String)>, usage: Option<TokenUsage>, finish: Option<String> }
impl ChatState {
    async fn add(&mut self, value: &Value, tx: &mpsc::Sender<Result<ResponseEvent, ApiError>>) -> Result<(), ApiError> {
        if value.get("error").is_some() { return Err(invalid("provider returned an error")); }
        if let Some(id) = value["id"].as_str() { self.id = id.to_owned(); }
        if let Some(usage) = value.get("usage").filter(|v| !v.is_null()) {
            if let (Some(input), Some(output), Some(total)) = (usage["prompt_tokens"].as_i64(), usage["completion_tokens"].as_i64(), usage["total_tokens"].as_i64()) {
                if input < 0 || output < 0 || total < 0 { return Err(invalid("negative usage")); }
                self.usage = Some(TokenUsage { input_tokens: input, output_tokens: output, total_tokens: total,
                    cached_input_tokens: usage["prompt_tokens_details"]["cached_tokens"].as_i64().unwrap_or(0),
                    reasoning_output_tokens: usage["completion_tokens_details"]["reasoning_tokens"].as_i64().unwrap_or(0), ..Default::default() });
            }
        }
        for choice in value["choices"].as_array().ok_or_else(|| invalid("missing choices"))? {
            if choice["index"].as_u64().unwrap_or(0) != 0 { return Err(invalid("multiple choices unsupported")); }
            if let Some(finish) = choice["finish_reason"].as_str() { self.finish = Some(finish.to_owned()); }
            let delta = choice.get("delta").or_else(|| choice.get("message")).ok_or_else(|| invalid("missing choice data"))?;
            if let Some(text) = delta["content"].as_str() {
                if self.text.is_empty() {
                    let item = serde_json::from_value(json!({"type":"message","id":format!("{}-message", self.id),"role":"assistant","content":[],"phase":"final_answer"})).map_err(invalid)?;
                    tx.send(Ok(ResponseEvent::OutputItemAdded(item))).await.map_err(invalid)?;
                }
                self.text.push_str(text);
                tx.send(Ok(ResponseEvent::OutputTextDelta(text.to_owned()))).await.map_err(invalid)?;
            }
            if let Some(calls) = delta["tool_calls"].as_array() {
                for (position, call) in calls.iter().enumerate() {
                    let index = call["index"].as_u64().unwrap_or(position as u64);
                    let entry = self.calls.entry(index).or_default();
                    if let Some(id) = call["id"].as_str() { entry.0.push_str(id); }
                    if let Some(name) = call["function"]["name"].as_str() { entry.1.push_str(name); }
                    if let Some(args) = call["function"]["arguments"].as_str() { entry.2.push_str(args); }
                }
            }
        }
        Ok(())
    }
    async fn finish(self, tx: &mpsc::Sender<Result<ResponseEvent, ApiError>>, custom: &HashSet<String>, namespaces: &ToolNamespaces) -> Result<(), ApiError> {
        if !matches!(self.finish.as_deref(), Some("stop" | "tool_calls")) { return Err(invalid("missing or unsuccessful finish reason")); }
        if self.id.is_empty() { return Err(invalid("missing completion identity")); }
        if !self.text.is_empty() {
            let item: ResponseItem = serde_json::from_value(json!({"type":"message","id":format!("{}-message", self.id),"role":"assistant","content":[{"type":"output_text","text":self.text}],"phase":"final_answer"})).map_err(invalid)?;
            tx.send(Ok(ResponseEvent::OutputItemDone(item))).await.map_err(invalid)?;
        }
        for (_, (id, name, arguments)) in self.calls {
            if id.is_empty() || name.is_empty() { return Err(invalid("incomplete tool identity")); }
            let mut item = if custom.contains(&name) {
                let args: Value = serde_json::from_str(&arguments).map_err(invalid)?;
                json!({"type":"custom_tool_call","call_id":id,"name":name,"input":args["input"].as_str().ok_or_else(|| invalid("missing custom input"))?})
            } else { json!({"type":"function_call","call_id":id,"name":name,"arguments":arguments}) };
            if let Some((namespace, native_name)) = namespaces.get(&name) {
                item["name"] = json!(native_name);
                item["namespace"] = json!(namespace);
            }
            let item: ResponseItem = serde_json::from_value(item).map_err(invalid)?;
            tx.send(Ok(ResponseEvent::OutputItemAdded(item.clone()))).await.map_err(invalid)?;
            tx.send(Ok(ResponseEvent::OutputItemDone(item))).await.map_err(invalid)?;
        }
        tx.send(Ok(ResponseEvent::Completed { response_id: self.id, token_usage: self.usage,
            usage_metadata: None, end_turn: Some(self.finish.as_deref() == Some("stop")) })).await.map_err(invalid)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_preserves_tool_pair_and_namespace() {
        let (body, custom, names) = chat_request(json!({"model":"Qwen3-Coder-Next",
            "instructions":"repair", "parallel_tool_calls":true,
            "input":[{"type":"function_call","call_id":"c","namespace":"functions","name":"shell","arguments":"{}"},
                     {"type":"function_call_output","call_id":"c","output":"text"}],
            "tools":[{"type":"namespace","name":"functions","tools":[{"type":"function","name":"shell","parameters":{"type":"object"}}]},
                     {"type":"custom","name":"apply_patch","description":"patch"}]})).unwrap();
        assert_eq!(body["messages"][1]["tool_calls"][0]["function"]["name"], "functions__shell");
        assert_eq!(body["messages"][2]["tool_call_id"], "c");
        assert!(custom.contains("apply_patch"));
        assert_eq!(names["functions__shell"], ("functions".to_string(), "shell".to_string()));
    }

    #[tokio::test]
    async fn fragments_and_usage_produce_native_completion() {
        let (tx, mut rx) = mpsc::channel(32);
        let mut state = ChatState::default();
        state.add(&json!({"id":"r","choices":[{"index":0,"delta":{"tool_calls":[
            {"index":0,"id":"c","function":{"name":"shell","arguments":"{"}}]}}]}), &tx).await.unwrap();
        state.add(&json!({"id":"r","choices":[{"index":0,"delta":{"tool_calls":[
            {"index":0,"function":{"arguments":"}"}}]},"finish_reason":"tool_calls"}]}), &tx).await.unwrap();
        state.add(&json!({"id":"r","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":3,"total_tokens":13,
            "prompt_tokens_details":{"cached_tokens":4},"completion_tokens_details":{"reasoning_tokens":2}}}), &tx).await.unwrap();
        state.finish(&tx, &HashSet::new(), &ToolNamespaces::new()).await.unwrap();
        assert!(matches!(rx.recv().await.unwrap().unwrap(), ResponseEvent::OutputItemAdded(_)));
        match rx.recv().await.unwrap().unwrap() {
            ResponseEvent::OutputItemDone(ResponseItem::FunctionCall { arguments, call_id, .. }) => {
                assert_eq!(arguments, "{}"); assert_eq!(call_id, "c");
            }
            _ => panic!("missing native function call"),
        }
        match rx.recv().await.unwrap().unwrap() {
            ResponseEvent::Completed { token_usage: Some(usage), end_turn, .. } => {
                assert_eq!(usage.total_tokens, 13);
                assert_eq!(usage.cached_input_tokens, 4);
                assert_eq!(usage.reasoning_output_tokens, 2);
                assert_eq!(end_turn, Some(false));
            }
            _ => panic!("missing actual usage"),
        }
    }

    #[tokio::test]
    async fn errors_and_incomplete_responses_fail_closed() {
        let (tx, _) = mpsc::channel(32);
        assert!(ChatState::default().add(&json!({"error":{"message":"busy"}}), &tx).await.is_err());
        assert!(ChatState::default().finish(&tx, &HashSet::new(), &ToolNamespaces::new()).await.is_err());
    }
}
