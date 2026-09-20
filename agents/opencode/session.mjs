// Native plugin boundary; the transport records usage but never rewrites requests.
import { readFileSync, writeFileSync, renameSync, existsSync } from "node:fs"
import { randomUUID } from "node:crypto"

function encode(messages) {
  const history = []
  const firstUser = messages.find(m => m.info.role === "user")?.info.id
  for (const message of messages) for (const part of message.parts) {
    const native = structuredClone({ info: message.info, part })
    const base = { id: part.id, role: message.info.role, text: "", native,
      editable: message.info.role !== "system" && message.info.id !== firstUser,
      tool_calls: [], tool_result: null }
    if (part.type === "text") {
      base.text = native.part.text; delete native.part.text
    }
    if (part.type === "tool") {
      base.tool_calls = [part.callID]
      // Output is carried separately; tool input and native status stay protected.
      delete native.part.state.output
      history.push(base)
      if (part.state.status === "completed" || part.state.status === "error") {
        history.push({ ...base, id: part.id + ":result", role: "tool", tool_calls: [],
          tool_result: part.callID, text: part.state.output ?? part.state.error ?? "",
          native: { ...native, result: true } })
      }
    } else history.push(base)
  }
  return history
}

function decode(history, template) {
  const messages = new Map()
  for (const mapped of history) {
    if (mapped.native.summary === true) {
      const source = template ?? history.find(m => m.role === "assistant" && m.native.info)?.native.info
      if (!source) throw new Error("No native assistant template for summary")
      const info = { ...structuredClone(source), id: mapped.id, role: "assistant" }
      messages.set(info.id, { info, parts: [{ id: mapped.id + ":text", type: "text", text: mapped.text,
        messageID: info.id, sessionID: info.sessionID, synthetic: true }] })
      continue
    }
    const { info, part, result } = structuredClone(mapped.native)
    if (!messages.has(info.id)) messages.set(info.id, { info, parts: [] })
    const message = messages.get(info.id)
    if (result) {
      const tool = message.parts.find(p => p.id === part.id)
      if (!tool) throw new Error("Missing native tool call")
      if (tool.state.status === "error") tool.state.error = mapped.text
      else tool.state.output = mapped.text
    } else {
      if (part.type === "text") part.text = mapped.text
      message.parts.push(part)
    }
  }
  return [...messages.values()]
}

export default async function TokenAnaSession({ client }) {
  const directory = process.env.TOKENANA_SESSION_DIRECTORY
  if (!directory) throw new Error("Missing method session directory")
  const config = JSON.parse(readFileSync(`${directory}/session-request.json`, "utf8"))
  let sequence = 0, initialized = false, finished = false, mainSession = null, queue = Promise.resolve()
  let previous = [], reminders = [], active = null
  const edits = new Map(), removed = new Set(), summaries = []
  let prepared = null
  function save(name, data) {
    writeFileSync(`${directory}/${name}.tmp`, JSON.stringify(data))
    renameSync(`${directory}/${name}.tmp`, `${directory}/${name}`)
  }
  async function request(kind, payload) {
    const number = String(++sequence).padStart(6, "0")
    const root = `${directory}/session-channel/${number}`
    save(`session-channel/${number}.request.json`, { kind, ...payload })
    const deadline = Date.now() + config.timeout * 1000
    while (!existsSync(`${root}.response.json`)) {
      if (Date.now() >= deadline || existsSync(`${directory}/session-channel/closed.json`))
        throw new Error("Method callback unavailable or timed out")
      await new Promise(resolve => setTimeout(resolve, 10))
    }
    const response = JSON.parse(readFileSync(`${root}.response.json`, "utf8"))
    if (response.error) throw new Error(`Method callback failed: ${response.error}`)
    return response
  }
  function overlay(history) {
    const output = []
    for (const m of history) {
      for (const summary of summaries) if (summary.anchor === m.id) output.push(summary.message)
      if (!removed.has(m.id)) output.push({ ...m, text: edits.get(m.id) ?? m.text })
    }
    return output
  }
  async function event(kind, history) {
    const result = await request(kind, { history })
    const retained = new Set(result.history.map(m => m.id))
    for (let i = 0; i < result.history.length; i++) {
      const m = result.history[i]
      if (m.native.summary === true && !summaries.some(s => s.message.id === m.id)) {
        const prior = result.history.slice(0, i).findLast(item => history.some(old => old.id === item.id))
        const start = prior ? history.findIndex(old => old.id === prior.id) + 1 : 0
        const anchor = history.slice(start).find(old => !retained.has(old.id))?.id
        if (!anchor) throw new Error("Summary has no removed span")
        summaries.push({ anchor, message: m })
      }
    }
    for (const m of history) if (!retained.has(m.id)) removed.add(m.id)
    for (const m of result.history) edits.set(m.id, m.text)
    if (result.reminder) reminders.push(result.reminder)
    previous = result.history
    if (result.terminate && kind !== "finish") throw new Error("TOKENANA_METHOD_TERMINATED")
    return result.history
  }
  async function native() {
    const response = await client.session.messages({ path: { id: mainSession }, throwOnError: true })
    if (!Array.isArray(response.data)) throw new Error("Missing native messages")
    return response.data
  }
  // Serialize tool callbacks from parallel tool execution and sampling hooks.
  function serial(work) {
    const pending = queue.then(work)
    queue = pending.catch(() => {})
    return pending
  }
  save("session-plugin.json", { initialized: true, main_session: null })
  return {
    "experimental.chat.messages.transform": (_input, output) => serial(async () => {
      if (!output.messages.length || finished) return
      const sessionID = output.messages[0].info.sessionID
      const session = await client.session.get({ path: { id: sessionID }, throwOnError: true })
      if (session.data?.parentID) return
      if (mainSession && mainSession !== sessionID) throw new Error("Unexpected second root session")
      mainSession = sessionID
      const pending = (await native()).filter(m => m.info.role === "assistant" &&
        !m.info.time?.completed && !m.info.summary && m.info.agent === "build")
      if (!pending.length) return // Native compaction has its own auxiliary call.
      if (pending.length !== 1) throw new Error("Ambiguous native sampling boundary")
      if (active === pending[0].info.id && prepared) {
        output.messages = structuredClone(prepared)
        return
      }
      const template = output.messages.find(m => m.info.role === "assistant")?.info
      let history = overlay(encode(output.messages))
      if (!initialized) { history = await event("initialize", history); initialized = true }
      if (active !== pending[0].info.id) {
        if (active) {
          history = await event("after_model", history)
          history = await event("after_tool", history)
        }
        history = await event("before_model", history)
        active = pending[0].info.id
      }
      output.messages = decode(history, template)
      const user = output.messages.findLast(m => m.info.role === "user")
      for (const text of reminders) {
        if (!user) throw new Error("No native user message for reminder")
        const id = `msg_${randomUUID().replaceAll("-", "")}`
        output.messages.push({ info: { ...user.info, id }, parts: [{ id: `prt_${randomUUID().replaceAll("-", "")}`,
          messageID: id, sessionID, type: "text", text, synthetic: true }] })
      }
      reminders = []
      await request("model_input", { request: { messages: output.messages,
        note: "Native messages before SDK conversion; wire payload in api-records" } })
      prepared = structuredClone(output.messages)
      save("session-plugin.json", { initialized: true, main_session: mainSession })
    }),
    "chat.headers": async (input, output) => {
      const session = await client.session.get({ path: { id: input.sessionID }, throwOnError: true })
      output.headers["x-tokenana-purpose"] = !session.data?.parentID && input.agent === "build" ? "main" : "agent_auxiliary"
    },
    event: ({ event: update }) => serial(async () => {
      if (!initialized || finished || update.type !== "session.idle" || update.properties.sessionID !== mainSession) return
      const history = overlay(encode(await native()))
      await event("after_model", history)
      await event("after_tool", previous)
      await event("finish", previous)
      finished = true
    }),
  }
}
