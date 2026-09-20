// Local-only plugin. No dependencies, event-based stopping, or upstream replacement.
import { readFileSync, writeFileSync, renameSync, appendFileSync } from "node:fs"
import { randomUUID } from "node:crypto"

export default async function TokenAnaTurnControl({ client }) {
  const directory = process.env.TOKENANA_CONTROL_DIRECTORY
  if (!directory) throw new Error("Missing TokenAna control directory")
  const budget = JSON.parse(readFileSync(`${directory}/control-request.json`, "utf8"))
  if (!Number.isInteger(budget.initial) || !Number.isInteger(budget.final) ||
      budget.initial <= 0 || budget.final < budget.initial) throw new Error("Invalid turn budget")
  const state = {
    version: "opencode-message-boundary-v1", initialized: true, main_session: null,
    initial_budget: budget.initial, final_budget: budget.final, active_budget: budget.initial,
    used_turns: 0, extensions: [], turns: [], termination_reason: "running",
  }
  function save() {
    writeFileSync(`${directory}/control.json.tmp`, JSON.stringify(state, null, 2))
    renameSync(`${directory}/control.json.tmp`, `${directory}/control.json`)
    appendFileSync(`${directory}/control-history.jsonl`, JSON.stringify(state) + "\n")
  }
  save()
  return {
    "experimental.chat.messages.transform": async (_input, output) => {
      const started = performance.now()
      try {
      // Empty compaction heads have no main-loop request to count.
      if (!output.messages.length) return
      const ids = new Set(output.messages.map((message) => message.info.sessionID))
      if (ids.size !== 1) throw new Error("Ambiguous message session at sampling boundary")
      const sessionID = [...ids][0]
      const session = await client.session.get({ path: { id: sessionID }, throwOnError: true })
      if (!session.data || session.data.id !== sessionID) throw new Error("Missing native session identity")
      if (session.data.parentID) return // Subagents retain their native behavior.
      if (state.main_session && state.main_session !== sessionID) throw new Error("Unexpected second root session")
      state.main_session = sessionID
      const native = await client.session.messages({ path: { id: sessionID }, throwOnError: true })
      if (!Array.isArray(native.data)) throw new Error("Missing native session messages")
      // The main loop persists its assistant placeholder BEFORE this hook. Compaction
      // invokes this hook BEFORE creating its summary message, after prior cleanup.
      const pending = native.data.filter(({ info }) => info.role === "assistant" &&
        info.sessionID === sessionID && !info.time?.completed && !info.summary && info.agent === "build")
      if (!pending.length) return // A compaction transform, not a main sampling step.
      if (pending.length !== 1) throw new Error("Ambiguous pending main assistant")
      const messageID = pending[0].info.id
      let turn = state.turns.find((item) => item.message_id === messageID)
      if (!turn) {
        if (state.used_turns >= state.active_budget) {
          if (!state.extensions.length && state.final_budget > state.active_budget) {
            state.extensions.push({ after_turn: state.used_turns, from: state.active_budget, to: state.final_budget })
            state.active_budget = state.final_budget
          } else {
            state.termination_reason = "budget_exhausted"
            state.blocked_message_id = messageID
            save()
            throw new Error("TOKENANA_TURN_BUDGET_EXHAUSTED")
          }
        }
        state.used_turns += 1
        turn = { message_id: messageID, number: state.used_turns, active_budget: state.active_budget }
        state.turns.push(turn)
      }
      const user = output.messages.findLast((message) => message.info.role === "user")
      if (!user) throw new Error("Missing user message at main sampling boundary")
      const reminder = `Turn budget: this is turn ${turn.number} of ${turn.active_budget}. ` +
        `You have ${turn.active_budget - turn.number + 1} turns including this one. ` +
        (state.extensions.length ? "The budget has been extended once; no further extension is available. " : "") +
        "Prioritize finishing the repository repair and required Git commits, then finish normally."
      turn.reminder = reminder
      const id = `msg_${randomUUID().replaceAll("-", "")}`
      output.messages.push({ info: { ...user.info, id }, parts: [{
        id: `prt_${randomUUID().replaceAll("-", "")}`, messageID: id, sessionID,
        type: "text", text: reminder, synthetic: true,
      }] })
      save() // Persist before returning to the native sampling path.
      } finally {
        appendFileSync(`${directory}/timing.jsonl`, JSON.stringify({ version: 1, event: "end",
          phase: "control_callback", utc_seconds: Date.now()/1000,
          seconds: (performance.now()-started)/1000 }) + "\n")
      }
    },
  }
}
