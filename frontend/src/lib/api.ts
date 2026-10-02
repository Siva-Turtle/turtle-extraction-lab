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
};

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
