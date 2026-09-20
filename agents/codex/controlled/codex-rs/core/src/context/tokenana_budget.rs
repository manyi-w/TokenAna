use super::ContextualUserFragment;
use codex_protocol::models::ContentItemKind;

pub(crate) struct TokenanaBudget {
    pub(crate) used: u32,
    pub(crate) limit: u32,
}

impl ContextualUserFragment for TokenanaBudget {
    fn role(&self) -> &'static str { "developer" }

    fn content_kind(&self) -> ContentItemKind {
        ContentItemKind("tokenana.remaining_turns".to_string())
    }

    fn markers(&self) -> (&'static str, &'static str) { Self::type_markers() }

    fn type_markers() -> (&'static str, &'static str) {
        ("<tokenana_budget>\n", "\n</tokenana_budget>")
    }

    fn body(&self) -> String {
        let remaining = self.limit - self.used;
        format!(
            "This is main-loop interaction {} of {}. After this interaction, {} remain. \
             Complete the task within this budget and follow its original submission instructions.",
            self.used, self.limit, remaining
        )
    }
}
