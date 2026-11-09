// IndexedDB is not part of jsdom — fake-indexeddb provides a real
// implementation so the lists store runs against real code.
import 'fake-indexeddb/auto';

import { beforeEach, describe, expect, it } from 'vitest';
import {
  deleteList,
  loadListings,
  loadLists,
  markListingWithdrawn,
  parseUrlList,
  saveList,
  saveListing,
} from './lists';

beforeEach(async () => {
  for (const list of await loadLists()) await deleteList(list.id);
});

describe('parseUrlList', () => {
  it('extracts one-URL-per-line lists', () => {
    const parsed = parseUrlList('https://one.example.com/\nhttps://two.example.com/page\n');
    expect(parsed.urls).toEqual(['https://one.example.com/', 'https://two.example.com/page']);
    expect(parsed.invalid).toBe(0);
  });

  it('extracts URLs from CSV, JSON and markdown mixes', () => {
    const parsed = parseUrlList(
      [
        'name,url',
        'One,https://csv.example.com/a',
        'Two,https://csv.example.com/b',
        '{"links": ["https://json.example.com/x", "https://json.example.com/y"]}',
        '- [Markdown](https://md.example.com/doc)',
      ].join('\n'),
    );
    expect(parsed.urls).toEqual([
      'https://csv.example.com/a',
      'https://csv.example.com/b',
      'https://json.example.com/x',
      'https://json.example.com/y',
      'https://md.example.com/doc',
    ]);
  });

  it('prepends https:// to bare hosts', () => {
    const parsed = parseUrlList('example.com/wiki/Nostr\nsub.domain.example.org');
    expect(parsed.urls).toEqual(['https://example.com/wiki/Nostr', 'https://sub.domain.example.org/']);
  });

  it('skips comments and prose silently', () => {
    const parsed = parseUrlList(
      ['# my bookmarks', 'These are some great sites I found', 'https://real.example.com/'].join('\n'),
    );
    expect(parsed.urls).toEqual(['https://real.example.com/']);
    expect(parsed.invalid).toBe(0);
  });

  it('counts URL-shaped garbage as invalid', () => {
    const parsed = parseUrlList('https://ok.example.com/\nftp:://broken\n...\n');
    expect(parsed.urls).toEqual(['https://ok.example.com/']);
    expect(parsed.invalid).toBe(1);
  });

  it('dedupes after normalization', () => {
    const parsed = parseUrlList(
      ['https://Dup.Example.com/', 'https://dup.example.com/#fragment', 'https://dup.example.com/'].join('\n'),
    );
    expect(parsed.urls).toHaveLength(1);
  });

  it('strips trailing punctuation from pasted links', () => {
    const parsed = parseUrlList('See https://punct.example.com/page. And https://punct.example.com/two,');
    expect(parsed.urls).toEqual(['https://punct.example.com/page', 'https://punct.example.com/two']);
  });
});

describe('lists store', () => {
  it('saves, loads and deletes a list', async () => {
    const list = await saveList({
      name: 'My list',
      urls: ['https://one.example.com/', 'https://two.example.com/'],
      source: 'paste',
    });
    expect(list.id).toBeTruthy();
    expect(list.name).toBe('My list');
    expect(list.urls).toHaveLength(2);

    const loaded = await loadLists();
    expect(loaded.some((l) => l.id === list.id)).toBe(true);

    await deleteList(list.id);
    expect((await loadLists()).some((l) => l.id === list.id)).toBe(false);
  });

  it('defaults the name when empty', async () => {
    const list = await saveList({ name: '   ', urls: ['https://one.example.com/'], source: 'file' });
    expect(list.name).toBe('Untitled list');
    expect(list.source).toBe('file');
  });

  it('carries service role/proto metadata', async () => {
    const list = await saveList({
      name: 'Echo (service)',
      urls: ['https://echo.example.com/'],
      source: 'paste',
      role: 'tool',
      proto: 'mcp',
    });
    expect(list.role).toBe('tool');
    expect(list.proto).toBe('mcp');
  });
});

describe('listings store', () => {
  it('saves and withdraws a listing', async () => {
    await saveListing({
      d: 'echo',
      name: 'Echo',
      role: 'tool',
      protocols: ['mcp'],
      price: 'free',
      publishedAt: 1_700_000_000_000,
      eventId: 'f'.repeat(64),
      pubkey: 'a'.repeat(64),
      status: 'active',
    });

    let listings = await loadListings();
    expect(listings.find((l) => l.d === 'echo')?.status).toBe('active');

    await markListingWithdrawn('echo');
    listings = await loadListings();
    expect(listings.find((l) => l.d === 'echo')?.status).toBe('withdrawn');
  });
});
