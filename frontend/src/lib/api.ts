import axios from "axios";

export const api = axios.create({ baseURL: "/api/v1" });

export type AttributeType = "string" | "number" | "boolean" | "enum" | "array" | "object";

export type AttributeObjectPropertyType = "string" | "number" | "boolean" | "array";

export type AttributeObjectProperty = {
  name: string;
  type: AttributeObjectPropertyType;
  null_allowed: boolean;
  enum?: string[];
  description?: string;
};

export type AttributeArrayKind = "string" | "number" | "object";

export type AttributeArrayItems = {
  kind: AttributeArrayKind;
  properties: AttributeObjectProperty[];
};

export type LogsQueryParams = {
  models?: string[];
  agent_ids?: string[];
  dates?: string[];
  clients?: string[];
  meeting_types?: string[];
  meeting_titles?: string[];
  reasoning_efforts?: string[];
};

export const REASONING_EFFORTS = ["max", "xhigh", "high", "medium", "low", "minimal", "none"] as const;

export type ModelReasoning = {
  supported_efforts?: string[] | null;
  default_effort?: string | null;
  default_enabled?: boolean | null;
  supports_max_tokens?: boolean | null;
  mandatory?: boolean | null;
};

export type ModelInfo = { id: string; name: string; reasoning?: ModelReasoning | null };

export type AgentKind = "extraction" | "identifier";

export type Agent = {
  id: string;
  name: string;
  description: string;
  kind: string;
  system_instruction: string;
  input_types: string[];
  is_enabled: boolean;
};

/**
 * Meeting Type derived from a meeting title.
 * Titles look like "<Client name> and Turtle | <Meeting Type>" — the type is
 * the text after the FIRST "|", trimmed. No "|" falls back to the full
 * trimmed title (bare-type titles, legacy "<>" titles); blank stays blank.
 */
export function meetingTypeOf(title: string | null | undefined): string {
  const s = (title ?? "").trim();
  if (!s) return "";
  const i = s.indexOf("|");
  if (i === -1) return s;
  return s.slice(i + 1).trim();
}
