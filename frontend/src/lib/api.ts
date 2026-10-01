import axios from "axios";

export const api = axios.create({ baseURL: "/api/v1" });

export type AttributeType = "string" | "number" | "boolean" | "enum" | "array" | "object";

export type AttributeObjectPropertyType = "string" | "number" | "boolean" | "array";

export type AttributeObjectProperty = {
  name: string;
  type: AttributeObjectPropertyType;
  null_allowed: boolean;
};
