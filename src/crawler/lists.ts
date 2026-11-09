/**
 * User-managed link lists + published capability listings.
 *
 * Replaces the bundled collections: users paste or upload their own link
 * lists, the app stores them locally (IndexedDB) and indexes them on demand.
 * A list can be marked as a SERVICE (role/proto) — jobs from it carry the
 * capability-discovery tags (spec v1.3 §9.2), and the matching kind 39698
 * announcement is recorded here so the UI can show / withdraw it later.
 *
 * Everything is device-local: lists never leave the browser except as the
 * public observations their URLs produce when crawled.
 */

import { openDB, type DBSchema, type IDBPDatabase } from 'idb';

import { normalizeIndexUrl } from '@sip01/protocol';

/** Hard cap on URLs per list — a bigger import is truncated (with a note). */
export const MAX_LIST_URLS = 50_000;

/** Hard cap on an uploaded file — protects the tab from a giant blob. */
export const MAX_LIST_FILE_BYTES = 10 * 1024 * 1024;

export interface LinkList {
  id: string;
  name: string;
  /** Normalized, deduped URLs (SIP-01 §7). */
  urls: string[];
  createdAt: number;
  updatedAt: number;
  source: 'paste' | 'file';
  /** When set, jobs from this list are indexed with these capability tags. */
  role?: string;
  proto?: string;
}

/** A kind 39698 capability announcement this device published. */
export interface PublishedListing {
  /** Capability id — the `d` tag. */
  d: string;
  name: string;
  role: string;
  protocols: string[];
  price: string;
  endpoint?: string;
  homepage?: string;
  publishedAt: number;
  eventId: string;
  pubkey: string;
  status: 'active' | 'withdrawn';
}

interface ListsDB extends DBSchema {
  lists: {
    key: string;
    value: LinkList;
  };
  listings: {
    key: string;
    value: PublishedListing;
  };
}

let db: IDBPDatabase<ListsDB> | null = null;
let cache: LinkList[] | null = null;
let listingCache: PublishedListing[] | null = null;

async function getDB(): Promise<IDBPDatabase<ListsDB>> {
  if (db) return db;
  db = await openDB<ListsDB>('indexstr-lists', 1, {
    upgrade(database) {
      database.createObjectStore('lists', { keyPath: 'id' });
      database.createObjectStore('listings', { keyPath: 'd' });
    },
  });
  return db;
}

function genId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  return uuid ?? `list-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

/* ------------------------------------------------------------------------ */
/* Lists                                                                     */
/* ------------------------------------------------------------------------ */

/**
 * Load lists into the in-memory cache (newest first). Cheap to call
 * repeatedly — the IndexedDB read happens once; mutations keep the cache
 * current. Storage failures (private mode, jsdom) degrade to [].
 */
export async function loadLists(): Promise<LinkList[]> {
  if (cache) return cache;
  try {
    const database = await getDB();
    const all = await database.getAll('lists');
    cache = all.sort((a, b) => b.createdAt - a.createdAt);
  } catch {
    cache = [];
  }
  return cache;
}

/** Synchronous view of the loaded lists ([] before loadLists resolves). */
export function getListsSync(): LinkList[] {
  return cache ?? [];
}

export async function saveList(input: {
  name: string;
  urls: string[];
  source: 'paste' | 'file';
  role?: string;
  proto?: string;
}): Promise<LinkList> {
  const database = await getDB();
  const now = Date.now();
  const list: LinkList = {
    id: genId(),
    name: input.name.trim().slice(0, 80) || 'Untitled list',
    urls: input.urls.slice(0, MAX_LIST_URLS),
    createdAt: now,
    updatedAt: now,
    source: input.source,
    ...(input.role ? { role: input.role } : {}),
    ...(input.proto ? { proto: input.proto } : {}),
  };
  await database.put('lists', list);
  cache = [list, ...(cache ?? [])];
  return list;
}

export async function deleteList(id: string): Promise<void> {
  const database = await getDB();
  await database.delete('lists', id);
  cache = (cache ?? []).filter((l) => l.id !== id);
}

/* ------------------------------------------------------------------------ */
/* Published capability listings                                             */
/* ------------------------------------------------------------------------ */

/** Load published listings (newest first). Storage failures degrade to []. */
export async function loadListings(): Promise<PublishedListing[]> {
  if (listingCache) return listingCache;
  try {
    const database = await getDB();
    const all = await database.getAll('listings');
    listingCache = all.sort((a, b) => b.publishedAt - a.publishedAt);
  } catch {
    listingCache = [];
  }
  return listingCache;
}

export function getListingsSync(): PublishedListing[] {
  return listingCache ?? [];
}

export async function saveListing(listing: PublishedListing): Promise<void> {
  const database = await getDB();
  await database.put('listings', listing);
  listingCache = [listing, ...(listingCache ?? []).filter((l) => l.d !== listing.d)];
}

export async function markListingWithdrawn(d: string): Promise<void> {
  const database = await getDB();
  const existing = await database.get('listings', d);
  if (!existing) return;
  const updated: PublishedListing = { ...existing, status: 'withdrawn' };
  await database.put('listings', updated);
  listingCache = (listingCache ?? []).map((l) => (l.d === d ? updated : l));
}

/* ------------------------------------------------------------------------ */
/* Parsing                                                                   */
/* ------------------------------------------------------------------------ */

const URL_RE = /https?:\/\/[^\s<>"'`)\]}]+/gi;
const BARE_HOST_RE =
  /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+(?:\/\S*)?$/i;

/**
 * Extract URLs from arbitrary pasted text or file content — one URL per
 * line, CSV cells, JSON strings, markdown links: all work, because we scan
 * for URL-shaped tokens rather than parsing a format. Bare hosts
 * ("example.com/path") get https:// prepended. Comment lines (#) are
 * skipped. Returns normalized, deduped URLs plus an invalid-candidate count.
 */
export function parseUrlList(text: string): {
  urls: string[];
  invalid: number;
  truncated: boolean;
} {
  const seen = new Set<string>();
  const urls: string[] = [];
  let invalid = 0;

  const add = (raw: string): void => {
    const cleaned = raw
      .trim()
      .replace(/^[(<[]+/, '')
      .replace(/[.,;:!?)\]}>'"`]+$/g, '');
    if (!cleaned) return;
    const normalized = normalizeIndexUrl(cleaned);
    if (!normalized) {
      invalid++;
      return;
    }
    if (!seen.has(normalized)) {
      seen.add(normalized);
      urls.push(normalized);
    }
  };

  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;

    const matches = trimmed.match(URL_RE);
    if (matches && matches.length > 0) {
      for (const match of matches) add(match);
      continue;
    }

    // Bare host / host+path (no scheme) — common in hand-written lists.
    if (!trimmed.includes(' ') && BARE_HOST_RE.test(trimmed)) {
      add(`https://${trimmed}`);
    } else if (!trimmed.includes(' ') && trimmed.includes('.')) {
      // Looks like it was meant to be a URL but failed the shape check.
      invalid++;
    }
    // Anything else is prose/comment — ignored silently.
  }

  const truncated = urls.length > MAX_LIST_URLS;
  return {
    urls: truncated ? urls.slice(0, MAX_LIST_URLS) : urls,
    invalid,
    truncated,
  };
}

/** Read an uploaded list file (size-capped) as text. */
export async function readListFile(file: File): Promise<string> {
  if (file.size > MAX_LIST_FILE_BYTES) {
    throw new Error(
      `File is too large (${Math.round(file.size / 1024 / 1024)} MB — max ${
        MAX_LIST_FILE_BYTES / 1024 / 1024
      } MB)`,
    );
  }
  return file.text();
}
