//! Explicit file callback channel for the independent TokenAna build.
use codex_protocol::error::{CodexErr, Result as CodexResult};
use codex_protocol::models::ResponseItem;
use codex_protocol::protocol::SessionSource;
use codex_history::ResponseItemEnvelope;
use serde_json::{Value, json};
use std::path::PathBuf;
use std::time::{Duration, Instant};
use super::Session;

pub(super) struct MethodSession {
    directory: PathBuf,
    sequence: usize,
    timeout: Duration,
    initialized: bool,
    reminders: Vec<String>,
}

fn failure(error: impl std::fmt::Display) -> CodexErr {
    CodexErr::Fatal(format!("TOKENANA_SESSION_ERROR: {error}"))
}

impl MethodSession {
    pub(super) fn open(source: &SessionSource) -> CodexResult<Option<Self>> {
        if !matches!(source, SessionSource::Exec) { return Ok(None); }
        let Some(directory) = std::env::var_os("TOKENANA_SESSION_DIRECTORY") else { return Ok(None); };
        let directory = PathBuf::from(directory);
        let request: Value = serde_json::from_slice(&std::fs::read(directory.join("session-request.json")).map_err(failure)?).map_err(failure)?;
        if request["version"] != "codex-session-compatible-v1" { return Err(failure("unsupported session version")); }
        let timeout = request["timeout"].as_f64().ok_or_else(|| failure("missing timeout"))?;
        if !timeout.is_finite() || timeout <= 0.0 || timeout > 3600.0 { return Err(failure("invalid timeout")); }
        std::fs::OpenOptions::new().write(true).create_new(true).open(directory.join("session-hook.ready")).map_err(failure)?;
        Ok(Some(Self { directory, sequence: 0, timeout: Duration::from_secs_f64(timeout), initialized: false, reminders: Vec::new() }))
    }

    async fn exchange(&mut self, payload: Value) -> CodexResult<Value> {
        self.sequence += 1;
        let stem = self.directory.join("session-channel").join(format!("{:06}", self.sequence));
        let temporary = stem.with_extension("tmp");
        let request = stem.with_extension("request.json");
        let response = stem.with_extension("response.json");
        if request.exists() || response.exists() { return Err(failure("session exchange reused")); }
        std::fs::write(&temporary, serde_json::to_vec(&payload).map_err(failure)?).map_err(failure)?;
        std::fs::rename(temporary, request).map_err(failure)?;
        let start = Instant::now();
        while !response.exists() {
            if start.elapsed() >= self.timeout || self.directory.join("session-channel/closed.json").exists() {
                return Err(failure("callback unavailable or timed out"));
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        let result: Value = serde_json::from_slice(&std::fs::read(response).map_err(failure)?).map_err(failure)?;
        if let Some(error) = result.get("error") { return Err(failure(error)); }
        Ok(result)
    }

    pub(super) async fn emit(&mut self, kind: &str, sess: &Session) -> CodexResult<()> {
        let snapshot = sess.clone_history().await;
        let original = snapshot.annotated_items();
        let items: Vec<_> = original.iter().map(|entry| entry.item.clone()).collect();
        let result = self.exchange(json!({"kind":kind,"native_history":items})).await?;
        let updated: Vec<ResponseItem> = serde_json::from_value(result["native_history"].clone()).map_err(failure)?;
        if items != updated {
            let mut envelopes = Vec::new();
            for item in updated {
                let value = serde_json::to_value(&item).map_err(failure)?;
                let previous = original.iter().find(|old| {
                    let old_value = serde_json::to_value(&old.item).ok();
                    old.item == item || old_value.as_ref().is_some_and(|old| {
                        old.get("type") == value.get("type") && ["id", "call_id"].iter().any(|key|
                            old.get(*key).is_some_and(|id| !id.is_null() && Some(id) == value.get(*key)))
                    })
                });
                let mut envelope = previous.cloned().unwrap_or_else(|| ResponseItemEnvelope::new(item.clone()));
                envelope.item = item;
                envelopes.push(envelope);
            }
            let mut state = sess.state.lock().await;
            let reference = state.reference_context_item();
            state.replace_annotated_history(envelopes, reference, crate::context_manager::HistoryReplacement::Reset);
        }
        if let Some(reminder) = result["reminder"].as_str() { self.reminders.push(reminder.to_owned()); }
        if kind != "finish" && result["terminate"].is_string() { return Err(failure("method termination requested")); }
        Ok(())
    }

    pub(super) async fn before(&mut self, sess: &Session) -> CodexResult<Vec<ResponseItem>> {
        if !self.initialized { self.emit("initialize", sess).await?; self.initialized = true; }
        self.emit("before_model", sess).await?;
        let mut reminders = Vec::new();
        for text in self.reminders.drain(..) {
            reminders.push(serde_json::from_value(json!({"type":"message","role":"user","content":[{"type":"input_text","text":text}]})).map_err(failure)?);
        }
        Ok(reminders)
    }

    pub(super) async fn input(&mut self, items: &[ResponseItem]) -> CodexResult<()> {
        self.exchange(json!({"kind":"model_input","request":{"input":items,"note":"Native sampling input; exact model wire payload in api-records"}})).await?;
        Ok(())
    }
}
