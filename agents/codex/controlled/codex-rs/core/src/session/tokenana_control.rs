//! Optional control build: one budget for one unattended exec invocation.
use std::fs::File;
use std::fs::OpenOptions;
use std::io::Write;
use std::path::PathBuf;

use codex_protocol::error::CodexErr;
use codex_protocol::error::Result as CodexResult;
use codex_protocol::protocol::SessionSource;
use serde::Deserialize;
use serde_json::json;

use crate::context::tokenana_budget::TokenanaBudget;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Request {
    version: String,
    profile: String,
}

pub(super) struct Control {
    log: File,
    timing: File,
    used: u32,
    initial: u32,
    final_limit: u32,
    limit: u32,
    extended: bool,
}

fn failure(error: impl std::fmt::Display) -> CodexErr {
    CodexErr::Fatal(format!("TOKENANA_CONTROL_ERROR: {error}"))
}

impl Control {
    pub(super) fn open(source: &SessionSource, turn_id: &str) -> CodexResult<Option<Self>> {
        // Auxiliary sessions inherit the process environment, but never own this budget.
        if !matches!(source, SessionSource::Exec) {
            return Ok(None);
        }
        let Some(directory) = std::env::var_os("TOKENANA_CONTROL_DIRECTORY") else {
            return Ok(None);
        };
        let directory = PathBuf::from(directory);
        let request: Request = serde_json::from_reader(
            File::open(directory.join("control-request.json")).map_err(failure)?,
        ).map_err(failure)?;
        if request.version != "codex-sampling-boundary-v1" {
            return Err(failure("unsupported control request version"));
        }
        let (initial, final_limit) = match request.profile.as_str() {
            "gpt" => (50, 67),
            "claude" => (52, 64),
            "gemini" => (29, 45),
            "deepswe_gpt" => (53, 74),
            _ => return Err(failure("unknown budget profile")),
        };
        // Refuse a second native turn/restart instead of silently resetting the budget.
        let log = OpenOptions::new().write(true).create_new(true)
            .open(directory.join("control-events.jsonl")).map_err(failure)?;
        let mut control = Self {
            log, timing: OpenOptions::new().create(true).append(true)
                .open(directory.join("timing.jsonl")).map_err(failure)?,
            used: 0, initial, final_limit, limit: initial, extended: false,
        };
        control.record("initialized", Some(turn_id))?;
        Ok(Some(control))
    }

    fn record(&mut self, event: &str, turn_id: Option<&str>) -> CodexResult<()> {
        let record = json!({
            "version": "codex-sampling-boundary-v1",
            "event": event,
            "turn_id": turn_id,
            "used_turns": self.used,
            "initial_budget": self.initial,
            "final_budget": self.final_limit,
            "current_budget": self.limit,
            "extended": self.extended,
        });
        serde_json::to_writer(&mut self.log, &record).map_err(failure)?;
        self.log.write_all(b"\n").map_err(failure)?;
        self.log.sync_data().map_err(failure)
    }

    pub(super) fn before_sampling(&mut self) -> CodexResult<TokenanaBudget> {
        let started = std::time::Instant::now();
        let result = self.before_sampling_inner();
        let record = json!({"version": 1, "event": "end", "phase": "control_callback",
            "seconds": started.elapsed().as_secs_f64(),
            "utc_seconds": std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
                .map_err(failure)?.as_secs_f64()});
        serde_json::to_writer(&mut self.timing, &record).map_err(failure)?;
        self.timing.write_all(b"\n").map_err(failure)?;
        self.timing.flush().map_err(failure)?;
        result
    }

    fn before_sampling_inner(&mut self) -> CodexResult<TokenanaBudget> {
        // This is reached only when native completion/stop checks request more work.
        if self.used == self.limit && !self.extended {
            self.limit = self.final_limit;
            self.extended = true;
            self.record("extended", None)?;
        }
        if self.used >= self.limit {
            self.record("budget_exhausted", None)?;
            return Err(CodexErr::Fatal("TOKENANA_TURN_BUDGET_EXHAUSTED".to_string()));
        }
        self.used += 1;
        // Persist before dispatch. A failed sampling interaction still consumes a turn.
        self.record("before_sampling", None)?;
        Ok(TokenanaBudget { used: self.used, limit: self.limit })
    }
}
