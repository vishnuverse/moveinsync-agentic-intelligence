// Mirrors backend/app/api/schemas.py's CHAT_MESSAGE_MAX_LEN exactly -- kept
// as one shared constant (not duplicated inline) so the input's maxLength,
// the mock client's guardrail, and the real backend's 422 all agree on the
// same number.
export const CHAT_MESSAGE_MAX_LEN = 2000;
