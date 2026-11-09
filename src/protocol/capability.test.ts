/**
 * ORBSTR capability announcements (kind 39698) — build/parse/validate.
 * Ported from sip-01-core's discovery/capability.test.ts (canonical module).
 */
import { describe, expect, it } from 'vitest';
import {
  CAPABILITY_KIND,
  buildCapabilityEvent,
  capabilityPointers,
  endpointAllowed,
  isPrice,
  isProto,
  isRole,
  parseCapabilityEvent,
  parseQuery,
  slugify,
  withdrawnEvent,
} from './capability';

function fullInput() {
  return {
    id: 'echo-tool',
    name: 'Echo',
    summary: 'Returns text.',
    about: 'Local test tool.',
    role: 'tool' as const,
    protocols: ['mcp' as const, 'http' as const],
    capabilities: ['echo', 'text'],
    price: 'free' as const,
    endpoint: 'https://example.com/mcp',
    homepage: 'https://example.com',
  };
}

describe('buildCapabilityEvent', () => {
  it('builds a valid kind 39698 event', () => {
    const draft = buildCapabilityEvent(fullInput());
    expect(draft?.kind).toBe(CAPABILITY_KIND);
    const tags = new Map((draft?.tags ?? []).map((tag) => [tag[0], tag[1]]));
    expect(tags.get('d')).toBe('echo-tool');
    expect(tags.get('v')).toBe('1');
    expect(tags.get('role')).toBe('tool');
    expect(tags.get('price')).toBe('free');
    expect(tags.get('endpoint')).toBe('https://example.com/mcp');
    expect(tags.get('r')).toBe('https://example.com');
    expect(tags.get('alt')).toBe('Capability: Echo');
    const protos = (draft?.tags ?? []).filter((tag) => tag[0] === 'proto').map((tag) => tag[1]);
    expect(protos).toEqual(['mcp', 'http']);
    const topics = (draft?.tags ?? []).filter((tag) => tag[0] === 't').map((tag) => tag[1]);
    expect(topics).toEqual(['echo', 'text']);
    const content = JSON.parse(draft!.content) as Record<string, string>;
    expect(content.name).toBe('Echo');
    expect(content.summary).toBe('Returns text.');
    expect(content.about).toBe('Local test tool.');
  });

  it('does not use kind 39697 fields (u / x / schema overloading)', () => {
    const draft = buildCapabilityEvent(fullInput());
    expect(draft?.tags.find((tag) => tag[0] === 'u')).toBeUndefined();
    expect(draft?.tags.find((tag) => tag[0] === 'x')).toBeUndefined();
  });

  it('rejects javascript endpoints', () => {
    expect(
      buildCapabilityEvent({ ...fullInput(), endpoint: 'javascript:alert(1)' }),
    ).toBeNull();
  });

  it('rejects an invalid id', () => {
    expect(buildCapabilityEvent({ ...fullInput(), id: 'Not Valid!' })).toBeNull();
  });

  it('rejects empty names, summaries and protocols', () => {
    expect(buildCapabilityEvent({ ...fullInput(), name: '' })).toBeNull();
    expect(buildCapabilityEvent({ ...fullInput(), summary: '' })).toBeNull();
    expect(buildCapabilityEvent({ ...fullInput(), protocols: [] })).toBeNull();
  });
});

describe('parseCapabilityEvent', () => {
  it('round-trips without losing the core fields', () => {
    const draft = buildCapabilityEvent(fullInput());
    const parsed = parseCapabilityEvent(
      {
        kind: draft!.kind,
        content: draft!.content,
        tags: draft!.tags,
        created_at: draft!.created_at,
        pubkey: 'a'.repeat(64),
      },
      'relay',
    );
    expect(parsed?.id).toBe('echo-tool');
    expect(parsed?.name).toBe('Echo');
    expect(parsed?.role).toBe('tool');
    expect(parsed?.protocols).toEqual(['mcp', 'http']);
    expect(parsed?.price).toBe('free');
    expect(parsed?.status).toBe('active');
  });

  it('rejects non-39698 events', () => {
    const draft = buildCapabilityEvent(fullInput());
    expect(
      parseCapabilityEvent(
        { kind: 39697, content: draft!.content, tags: draft!.tags, created_at: 0, pubkey: 'a'.repeat(64) },
        'relay',
      ),
    ).toBeNull();
  });

  it('parses a withdrawn replacement', () => {
    const draft = withdrawnEvent('echo-tool');
    const parsed = parseCapabilityEvent(
      {
        kind: draft!.kind,
        content: draft!.content,
        tags: draft!.tags,
        created_at: draft!.created_at,
        pubkey: 'a'.repeat(64),
      },
      'relay',
    );
    expect(parsed?.status).toBe('withdrawn');
  });
});

describe('helpers', () => {
  it('slugify produces valid ids', () => {
    expect(slugify('Echo Tool')).toBe('echo-tool');
    expect(slugify('  Weird --- Name!!  ')).toBe('weird-name');
    expect(slugify('!!!')).toBe('service');
  });

  it('endpointAllowed accepts https/wss only', () => {
    expect(endpointAllowed('https://example.com')).toBe(true);
    expect(endpointAllowed('wss://example.com/ws')).toBe(true);
    expect(endpointAllowed('http://example.com')).toBe(false);
    expect(endpointAllowed('javascript:alert(1)')).toBe(false);
    expect(endpointAllowed('not a url')).toBe(false);
  });

  it('parseQuery extracts role/proto/price filters', () => {
    const parts = parseQuery('echo tool role:tool proto:mcp price:free');
    expect(parts.text).toBe('echo tool');
    expect(parts.role).toBe('tool');
    expect(parts.proto).toBe('mcp');
    expect(parts.price).toBe('free');
  });

  it('capabilityPointers finds 39698 a-tags only', () => {
    const tags = [
      ['a', '39698:' + 'a'.repeat(64) + ':echo'],
      ['a', '31990:' + 'b'.repeat(64) + ':handler'],
    ];
    expect(capabilityPointers(tags)).toEqual(['39698:' + 'a'.repeat(64) + ':echo']);
  });

  it('type guards validate the enums', () => {
    expect(isRole('tool')).toBe(true);
    expect(isRole('nope')).toBe(false);
    expect(isProto('mcp')).toBe(true);
    expect(isProto('carrier-pigeon')).toBe(false);
    expect(isPrice('freemium')).toBe(true);
    expect(isPrice('expensive')).toBe(false);
  });
});
