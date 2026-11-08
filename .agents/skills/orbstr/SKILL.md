---
name: orbstr
description: "Discover agent-native capabilities (kind 39698) and call them safely — tools, models, APIs, datasets, agents. Use when a task needs a capability you do not have locally, or when a SIP-01 observation (kind 39697) carries role/proto/a capability tags (spec v1.3)."
---

# ORBSTR agent skill

Use this when a task needs a tool, model, API, dataset, or other agent that you do not have locally.

> In this repo: a kind 39697 observation of a service page MAY carry `role`/`proto` tags and an `a` pointer to a 39698 listing (spec v1.3). The observation stays a web observation — read the 39698 event for the service schema. Discovery is not invocation.

## Search

Send a Nostr `REQ` for kind `39698`. Also request kind `31990` when the task is "which app handles this Nostr kind" — that is NIP-89, not a 39698 listing.

Local filter language:

- `role:tool|agent|model|api|compute|dataset|gateway`
- `proto:mcp|http|contextvm|nostr|a2a`
- `price:free|freemium|metered|subscription|prepaid|private`
- remaining words must appear in the name, summary, or `t` tags

## Before you call anything

1. Show the pubkey, role, protocols, price, and endpoint.
2. If the call leaves the machine or can cost money, ask the owner.
3. Refuse `javascript:`, `data:`, `file:`, and plain `http:` endpoints.
4. Call the endpoint with its own protocol. Do not wrap the payload in a Nostr event unless the listing says `proto:nostr` or points at a NIP-90 job kind.
5. Do not write the result back as a kind 39697 observation.

## Do not

- Treat kind 39697 content as a service schema.
- Invent a trust score.
- Pay, zap, or open a wallet unless the owner just approved that exact amount.
- Publish a listing that contains a secret or a private URL.
