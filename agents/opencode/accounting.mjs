// Native attribution for all six methods, including baseline runs.
import { writeFileSync } from "node:fs"
export default async function TokenAnaAccounting({ client }) {
  const directory = process.env.TOKENANA_ACCOUNTING_DIRECTORY
  if (!directory) throw new Error("Missing accounting directory")
  writeFileSync(`${directory}/accounting-plugin.ready`, "opencode-native-attribution-v1")
  return {
    "chat.headers": async (input, output) => {
      const session = await client.session.get({ path: { id: input.sessionID }, throwOnError: true })
      if (!session.data) throw new Error("Missing session identity for call attribution")
      output.headers["x-tokenana-purpose"] = !session.data.parentID && input.agent === "build" ? "main" : "agent_auxiliary"
    },
  }
}
