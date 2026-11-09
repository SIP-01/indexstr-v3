/**
 * ORBSTR capability announcements.
 *
 * Kind 39698 is a SIP-01 companion, not a change to kind 39697.
 * Checked unused on 2026-11-07 against nostr-protocol/nips README and
 * nostr-protocol/registry-of-kinds schema.yaml (39xxx already taken there:
 * 39000–39005, 39067, 39089, 39092, 39701). 39697 remains Web Index Observations.
 *
 * Reused, not reinvented: NIP-01 addressable events, `d` / `t` / `a` / `p`,
 * NIP-31 `alt`, NIP-40 `expiration`, NIP-19 for display. NIP-89 kind 31990
 * stays the handler directory. NIP-82 kind 32267 stays software packages.
 * NIP-90 stays job transport. NIP-99 stays classifieds.
 */

export const CAPABILITY_KIND = 39698;
export const WEB_INDEX_KIND = 39697;
export const HANDLER_KIND = 31990;
export const CAPABILITY_SCHEMA = "1";

export const ROLES = ["agent", "tool", "model", "api", "compute", "dataset", "gateway"] as const;
export const PROTOS = ["mcp", "http", "contextvm", "nostr", "a2a"] as const;
export const PRICES = ["free", "freemium", "metered", "subscription", "prepaid", "private"] as const;

export type Role = (typeof ROLES)[number];
export type Proto = (typeof PROTOS)[number];
export type Price = (typeof PRICES)[number];
export type ListingSource = "reference" | "local" | "relay";

const ID_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const TOPIC_RE = /^[a-z0-9][a-z0-9-]{0,49}$/;
const HEX64 = /^[0-9a-f]{64}$/;

export interface CapabilityInput {
  id: string;
  name: string;
  summary: string;
  about?: string;
  role: Role;
  protocols: Proto[];
  capabilities: string[];
  endpoint?: string;
  homepage?: string;
  price: Price;
  priceNote?: string;
  nip89?: string;
  expiration?: number;
}

export interface Capability {
  id: string;
  name: string;
  summary: string;
  about: string;
  role: Role;
  protocols: Proto[];
  capabilities: string[];
  endpoint?: string;
  homepage?: string;
  price: Price;
  priceNote?: string;
  nip89?: string;
  expiration?: number;
  status: "active" | "withdrawn";
  pubkey: string;
  createdAt: number;
  source: ListingSource;
  relay?: string;
  raw?: UnsignedEvent;
}

export interface UnsignedEvent {
  kind: number;
  content: string;
  tags: string[][];
  created_at: number;
  pubkey?: string;
}

export interface QueryParts {
  text: string;
  role?: Role;
  proto?: Proto;
  price?: Price;
}

export function isRole(value: string): value is Role {
  return (ROLES as readonly string[]).includes(value);
}
export function isProto(value: string): value is Proto {
  return (PROTOS as readonly string[]).includes(value);
}
export function isPrice(value: string): value is Price {
  return (PRICES as readonly string[]).includes(value);
}

export function parseQuery(input: string): QueryParts {
  const parts = input.trim().split(/\s+/).filter(Boolean);
  const free: string[] = [];
  let role: Role | undefined;
  let proto: Proto | undefined;
  let price: Price | undefined;
  for (const part of parts) {
    const [key, value] = part.split(":");
    if (value && key === "role" && isRole(value)) role = value;
    else if (value && key === "proto" && isProto(value)) proto = value;
    else if (value && key === "price" && isPrice(value)) price = value;
    else free.push(part);
  }
  return { text: free.join(" "), role, proto, price };
}

export function endpointAllowed(value: string, allowLocal = false): boolean {
  try {
    const url = new URL(value);
    if (url.protocol === "https:" || url.protocol === "wss:") return true;
    if (allowLocal && url.protocol === "local:") return true;
    return false;
  } catch {
    return false;
  }
}

export function slugify(name: string): string {
  const slug = name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
  return ID_RE.test(slug) ? slug : "service";
}

function topics(values: string[]): string[] {
  const out: string[] = [];
  for (const value of values) {
    const topic = value.toLowerCase().trim().replace(/\s+/g, "-");
    if (TOPIC_RE.test(topic) && !out.includes(topic)) out.push(topic);
  }
  return out.slice(0, 12);
}

export function buildCapabilityEvent(input: CapabilityInput, now = Math.floor(Date.now() / 1000)): UnsignedEvent | null {
  const id = input.id.trim().toLowerCase();
  const name = input.name.trim().slice(0, 80);
  const summary = input.summary.trim().slice(0, 240);
  if (!ID_RE.test(id) || !name || !summary) return null;
  if (!isRole(input.role) || !isPrice(input.price)) return null;
  const protocols = input.protocols.filter(isProto);
  if (protocols.length === 0) return null;
  if (input.endpoint && !endpointAllowed(input.endpoint)) return null;
  if (input.homepage && !endpointAllowed(input.homepage)) return null;
  if (input.nip89 && !/^\d+:[0-9a-f]{64}:[a-zA-Z0-9_-]+$/.test(input.nip89)) return null;

  const content: Record<string, string> = { name, summary };
  if (input.about?.trim()) content.about = input.about.trim().slice(0, 2000);
  if (input.priceNote?.trim()) content.priceNote = input.priceNote.trim().slice(0, 240);

  const tags: string[][] = [
    ["d", id],
    ["v", CAPABILITY_SCHEMA],
    ["role", input.role],
    ...protocols.map((proto) => ["proto", proto]),
    ["price", input.price],
    ...topics(input.capabilities).map((topic) => ["t", topic]),
  ];
  if (input.endpoint) tags.push(["endpoint", input.endpoint]);
  if (input.homepage) tags.push(["r", input.homepage]);
  if (input.nip89) tags.push(["a", input.nip89]);
  if (input.expiration && Number.isFinite(input.expiration)) {
    tags.push(["expiration", String(Math.floor(input.expiration))]);
  }
  tags.push(["alt", `Capability: ${name}`]);

  return { kind: CAPABILITY_KIND, created_at: now, content: JSON.stringify(content), tags };
}

export function withdrawnEvent(id: string, now = Math.floor(Date.now() / 1000)): UnsignedEvent | null {
  if (!ID_RE.test(id)) return null;
  return {
    kind: CAPABILITY_KIND,
    created_at: now,
    content: JSON.stringify({ name: id, summary: "Withdrawn" }),
    tags: [
      ["d", id],
      ["v", CAPABILITY_SCHEMA],
      ["status", "withdrawn"],
      ["alt", `Capability withdrawn: ${id}`],
    ],
  };
}

function tagValues(tags: string[][], name: string): string[] {
  return tags.filter((tag) => tag[0] === name && tag[1]).map((tag) => tag[1]);
}

export function parseCapabilityEvent(
  event: {
    kind: number;
    content: string;
    tags: string[][];
    created_at: number;
    pubkey: string;
  },
  source: ListingSource,
  relay?: string,
): Capability | null {
  if (event.kind !== CAPABILITY_KIND) return null;
  if (!HEX64.test(event.pubkey)) return null;
  const version = tagValues(event.tags, "v")[0];
  const id = tagValues(event.tags, "d")[0]?.toLowerCase();
  if (version !== CAPABILITY_SCHEMA || !id || !ID_RE.test(id)) return null;
  if (tagValues(event.tags, "status")[0] === "withdrawn") {
    return {
      id,
      name: id,
      summary: "Withdrawn by the publisher.",
      about: "",
      role: "tool",
      protocols: ["nostr"],
      capabilities: [],
      price: "free",
      status: "withdrawn",
      pubkey: event.pubkey,
      createdAt: event.created_at,
      source,
      relay,
    };
  }
  let parsed: Record<string, unknown>;
  try {
    parsed = JSON.parse(event.content) as Record<string, unknown>;
  } catch {
    return null;
  }
  const name = typeof parsed.name === "string" ? parsed.name.trim() : "";
  const summary = typeof parsed.summary === "string" ? parsed.summary.trim() : "";
  const role = tagValues(event.tags, "role")[0] ?? "";
  const price = tagValues(event.tags, "price")[0] ?? "";
  const protocols = tagValues(event.tags, "proto").filter(isProto);
  if (!name || !summary || !isRole(role) || !isPrice(price) || protocols.length === 0) return null;
  const endpoint = tagValues(event.tags, "endpoint")[0];
  const homepage = tagValues(event.tags, "r")[0];
  if (endpoint && !endpointAllowed(endpoint)) return null;
  if (homepage && !endpointAllowed(homepage)) return null;
  const expiration = Number(tagValues(event.tags, "expiration")[0]);
  return {
    id,
    name: name.slice(0, 80),
    summary: summary.slice(0, 240),
    about: typeof parsed.about === "string" ? parsed.about.slice(0, 2000) : "",
    role,
    protocols,
    capabilities: tagValues(event.tags, "t").filter((topic) => TOPIC_RE.test(topic)).slice(0, 12),
    endpoint,
    homepage,
    price,
    priceNote: typeof parsed.priceNote === "string" ? parsed.priceNote.slice(0, 240) : undefined,
    nip89: tagValues(event.tags, "a").find((value) => value.startsWith("31990:")),
    expiration: Number.isFinite(expiration) ? expiration : undefined,
    status: "active",
    pubkey: event.pubkey,
    createdAt: event.created_at,
    source,
    relay,
  };
}

export function listingKey(item: Pick<Capability, "pubkey" | "id">): string {
  return `${item.pubkey}:${item.id}`;
}

export function matches(item: Capability, query: QueryParts): boolean {
  if (item.status !== "active") return false;
  if (query.role && item.role !== query.role) return false;
  if (query.proto && !item.protocols.includes(query.proto)) return false;
  if (query.price && item.price !== query.price) return false;
  if (!query.text) return true;
  const hay = [item.name, item.summary, item.about, item.role, ...item.protocols, ...item.capabilities]
    .join(" ")
    .toLowerCase();
  return query.text
    .toLowerCase()
    .split(/\s+/)
    .every((word) => hay.includes(word));
}

/** Standard NIP-01 `a` coordinates pointing at capability announcements. */
export function capabilityPointers(tags: string[][]): string[] {
  return tags
    .filter((tag) => tag[0] === "a" && tag[1]?.startsWith(`${CAPABILITY_KIND}:`))
    .map((tag) => tag[1]);
}
