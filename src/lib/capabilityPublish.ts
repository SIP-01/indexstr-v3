/**
 * ORBSTR capability announcements (kind 39698) — build, sign, publish.
 *
 * App-layer, deliberately ISOLATED from the core's relay-health accounting:
 * a relay that validates kind 39697 may policy-reject 39698 ("kind not
 * allowed"), and that must never gate the relay for ordinary observations.
 * Best-effort fan-out; the caller reports the delivered count and can retry.
 */

import type { NostrEvent } from '@nostrify/nostrify';
import { finalizeEvent } from 'nostr-tools/pure';
import { getIndexerIdentity, getIndexerSecretKey } from '@sip01/protocol';
import { buildCapabilityEvent, withdrawnEvent, type CapabilityInput } from '@/protocol/capability';

export interface CapabilityPublishResult {
  /** NIP-01 addressable coordinate: 39698:<pubkey>:<d>. */
  coordinate: string;
  eventId: string;
  /** Relays that accepted the event (0 = nothing accepted it). */
  delivered: number;
}

type PublishFn = (relayUrl: string, event: NostrEvent) => Promise<void>;

async function fanOut(signed: NostrEvent, publish: PublishFn, relays: string[]): Promise<number> {
  const results = await Promise.allSettled(relays.map((url) => publish(url, signed)));
  return results.filter((result) => result.status === 'fulfilled').length;
}

function sign(template: { kind: number; created_at: number; tags: string[][]; content: string }): NostrEvent {
  return finalizeEvent(
    {
      kind: template.kind,
      created_at: template.created_at,
      tags: template.tags,
      content: template.content,
    },
    getIndexerSecretKey(),
  ) as NostrEvent;
}

/**
 * Build, sign, and publish a kind 39698 capability announcement for a
 * service the user owns. Signed by this device's indexer identity — the
 * same pseudonymous key that signs observations.
 */
export async function publishCapabilityEvent(
  input: CapabilityInput,
  publish: PublishFn,
  relays: string[],
): Promise<CapabilityPublishResult | null> {
  const template = buildCapabilityEvent(input);
  if (!template) return null;

  const signed = sign(template);
  const delivered = await fanOut(signed, publish, relays);
  return {
    coordinate: `39698:${getIndexerIdentity().pubkeyHex}:${input.id.trim().toLowerCase()}`,
    eventId: signed.id,
    delivered,
  };
}

/**
 * Withdraw a capability announcement: publish a replacement with the same
 * `d` and ["status","withdrawn"] (the addressable update — not a delete).
 */
export async function withdrawCapabilityListing(
  id: string,
  publish: PublishFn,
  relays: string[],
): Promise<CapabilityPublishResult | null> {
  const template = withdrawnEvent(id);
  if (!template) return null;

  const signed = sign(template);
  const delivered = await fanOut(signed, publish, relays);
  return {
    coordinate: `39698:${getIndexerIdentity().pubkeyHex}:${id}`,
    eventId: signed.id,
    delivered,
  };
}
