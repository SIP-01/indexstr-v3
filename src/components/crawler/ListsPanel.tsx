/**
 * Lists panel — bring-your-own link lists + service submissions.
 *
 * Replaces the bundled seed corpora: paste or upload your own link lists,
 * save them locally, and index them on demand. A service submission
 * publishes a kind 39698 capability announcement (ORBSTR companion) and
 * indexes the service's pages with role/proto tags so agents can find it.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  Ban,
  CheckCircle2,
  FileText,
  Globe,
  KeyRound,
  Link2,
  Loader2,
  Play,
  Rocket,
  Send,
  Trash2,
  Upload,
  X,
  XCircle,
} from 'lucide-react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Label } from '@/components/ui/label';
import { Progress } from '@/components/ui/progress';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import {
  PRICES,
  PROTOS,
  ROLES,
  slugify,
  type CapabilityInput,
  type Price,
  type Proto,
  type Role,
} from '@/protocol/capability';
import {
  deleteList,
  loadListings,
  loadLists,
  markListingWithdrawn,
  parseUrlList,
  readListFile,
  saveList,
  saveListing,
  type LinkList,
  type PublishedListing,
} from '@/crawler/lists';
import { cn } from '@/lib/utils';

interface Status {
  kind: 'ok' | 'warn' | 'err';
  text: string;
}

function StatusLine({ status }: { status: Status | null }) {
  if (!status) return null;
  const Icon = status.kind === 'ok' ? CheckCircle2 : status.kind === 'warn' ? AlertTriangle : XCircle;
  return (
    <p
      className={cn(
        'text-xs flex items-start gap-1.5',
        status.kind === 'ok' ? 'text-primary' : status.kind === 'warn' ? 'text-chart-4' : 'text-destructive',
      )}
    >
      <Icon className="h-3.5 w-3.5 shrink-0 mt-px" />
      <span>{status.text}</span>
    </p>
  );
}

export interface ListsPanelProps {
  ready: boolean;
  isRunning: boolean;
  onStart: () => void;
  onIndexList: (
    urls: string[],
    opts: { role?: string; proto?: string; followLinks?: boolean },
    onProgress?: (done: number, total: number) => void,
  ) => Promise<{ added: number; refused: number }>;
  onListsChanged: () => void;
  onPublishCapability: (
    input: CapabilityInput,
  ) => Promise<{ delivered: number; coordinate: string; eventId: string } | null>;
  onWithdrawCapability: (d: string) => Promise<{ delivered: number; coordinate: string } | null>;
}

export function ListsPanel({
  ready,
  isRunning,
  onStart,
  onIndexList,
  onListsChanged,
  onPublishCapability,
  onWithdrawCapability,
}: ListsPanelProps) {
  /* ---- Add links state ---- */
  const [addName, setAddName] = useState('');
  const [addText, setAddText] = useState('');
  const [addFile, setAddFile] = useState<File | null>(null);
  const [addFileParsed, setAddFileParsed] = useState<{ urls: number; invalid: number } | null>(null);
  const [addStatus, setAddStatus] = useState<Status | null>(null);
  const [addBusy, setAddBusy] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  /* ---- Lists state ---- */
  const [lists, setLists] = useState<LinkList[]>([]);
  const [followLinks, setFollowLinks] = useState(false);
  const [indexing, setIndexing] = useState<Record<string, { done: number; total: number }>>({});
  const [indexStatus, setIndexStatus] = useState<Status | null>(null);

  /* ---- Service form state ---- */
  const [svcName, setSvcName] = useState('');
  const [svcSummary, setSvcSummary] = useState('');
  const [svcUrls, setSvcUrls] = useState('');
  const [svcRole, setSvcRole] = useState<Role>('tool');
  const [svcProtos, setSvcProtos] = useState<Proto[]>(['mcp']);
  const [svcPrice, setSvcPrice] = useState<Price>('free');
  const [svcEndpoint, setSvcEndpoint] = useState('');
  const [svcHomepage, setSvcHomepage] = useState('');
  const [svcTags, setSvcTags] = useState('');
  const [svcAbout, setSvcAbout] = useState('');
  const [svcStatus, setSvcStatus] = useState<Status | null>(null);
  const [svcBusy, setSvcBusy] = useState(false);

  /* ---- Listings state ---- */
  const [listings, setListings] = useState<PublishedListing[]>([]);
  const [listingStatus, setListingStatus] = useState<Status | null>(null);

  const reloadLists = useCallback(async () => {
    setLists(await loadLists());
  }, []);

  const reloadListings = useCallback(async () => {
    setListings(await loadListings());
  }, []);

  useEffect(() => {
    const load = async () => {
      setLists(await loadLists());
      setListings(await loadListings());
    };
    void load();
  }, []);

  /* ------------------------------------------------------------------ */
  /* Add links                                                           */
  /* ------------------------------------------------------------------ */

  const textPreview = addFile ? null : parseUrlList(addText);

  const handleFilePick = async (file: File | null) => {
    setAddStatus(null);
    setAddFile(file);
    setAddFileParsed(null);
    if (!file) return;
    try {
      const text = await readListFile(file);
      const parsed = parseUrlList(text);
      setAddFileParsed({ urls: parsed.urls.length, invalid: parsed.invalid });
    } catch (error) {
      setAddFile(null);
      setAddStatus({ kind: 'err', text: error instanceof Error ? error.message : 'Could not read file' });
    }
  };

  const handleSaveList = async () => {
    setAddStatus(null);
    setAddBusy(true);
    try {
      let source: 'paste' | 'file' = 'paste';
      let text = addText;
      if (addFile) {
        source = 'file';
        text = await readListFile(addFile);
      }
      const parsed = parseUrlList(text);
      if (parsed.urls.length === 0) {
        setAddStatus({ kind: 'err', text: 'No usable URLs found — paste links or pick a file first.' });
        return;
      }
      const list = await saveList({ name: addName, urls: parsed.urls, source });
      setAddName('');
      setAddText('');
      setAddFile(null);
      setAddFileParsed(null);
      if (fileInputRef.current) fileInputRef.current.value = '';
      await reloadLists();
      onListsChanged();
      setAddStatus({
        kind: 'ok',
        text: `Saved "${list.name}" — ${list.urls.length.toLocaleString()} URLs${
          parsed.invalid > 0 ? ` (${parsed.invalid} invalid skipped)` : ''
        }${parsed.truncated ? ' · truncated to 50,000' : ''}. Press Index now to queue it.`,
      });
    } catch (error) {
      setAddStatus({ kind: 'err', text: error instanceof Error ? error.message : 'Could not save list' });
    } finally {
      setAddBusy(false);
    }
  };

  /* ------------------------------------------------------------------ */
  /* Index a list                                                        */
  /* ------------------------------------------------------------------ */

  const indexList = async (list: LinkList) => {
    setIndexStatus(null);
    setIndexing((prev) => ({ ...prev, [list.id]: { done: 0, total: list.urls.length } }));
    try {
      const result = await onIndexList(
        list.urls,
        { role: list.role, proto: list.proto, followLinks },
        (done, total) => setIndexing((prev) => ({ ...prev, [list.id]: { done, total } })),
      );
      setIndexStatus({
        kind: result.added > 0 ? 'ok' : 'warn',
        text: `"${list.name}": ${result.added.toLocaleString()} queued${
          result.refused > 0 ? `, ${result.refused.toLocaleString()} refused (non-public or invalid)` : ''
        }.${result.added > 0 && !isRunning ? ' Press Start Crawling to begin.' : ''}`,
      });
    } catch (error) {
      setIndexStatus({ kind: 'err', text: error instanceof Error ? error.message : 'Indexing failed' });
    } finally {
      setIndexing((prev) => {
        const next = { ...prev };
        delete next[list.id];
        return next;
      });
    }
  };

  const handleDeleteList = async (list: LinkList) => {
    await deleteList(list.id);
    await reloadLists();
    onListsChanged();
    setIndexStatus({ kind: 'ok', text: `Deleted "${list.name}".` });
  };

  /* ------------------------------------------------------------------ */
  /* Service submission                                                  */
  /* ------------------------------------------------------------------ */

  const toggleProto = (proto: Proto) => {
    setSvcProtos((prev) => (prev.includes(proto) ? prev.filter((p) => p !== proto) : [...prev, proto]));
  };

  const handleSubmitService = async () => {
    setSvcStatus(null);
    const urls = parseUrlList(svcUrls).urls;
    if (!svcName.trim() || !svcSummary.trim()) {
      setSvcStatus({ kind: 'err', text: 'Name and summary are required.' });
      return;
    }
    if (svcProtos.length === 0) {
      setSvcStatus({ kind: 'err', text: 'Pick at least one protocol.' });
      return;
    }
    if (urls.length === 0) {
      setSvcStatus({ kind: 'err', text: 'Add at least one service URL to index.' });
      return;
    }

    const input: CapabilityInput = {
      id: slugify(svcName),
      name: svcName.trim(),
      summary: svcSummary.trim(),
      ...(svcAbout.trim() ? { about: svcAbout.trim() } : {}),
      role: svcRole,
      protocols: svcProtos,
      capabilities: svcTags
        .split(/[,\s]+/)
        .map((t) => t.trim())
        .filter(Boolean),
      ...(svcEndpoint.trim() ? { endpoint: svcEndpoint.trim() } : {}),
      ...(svcHomepage.trim() ? { homepage: svcHomepage.trim() } : {}),
      price: svcPrice,
    };

    setSvcBusy(true);
    try {
      const published = await onPublishCapability(input);
      if (!published) {
        setSvcStatus({
          kind: 'err',
          text: 'The listing is invalid — check the endpoint/homepage URLs (https:// or wss:// only) and the name.',
        });
        return;
      }

      // Save the service URLs as a list so they can be re-indexed later.
      const list = await saveList({
        name: `${input.name} (service)`,
        urls,
        source: 'paste',
        role: svcRole,
        proto: svcProtos[0],
      });
      await reloadLists();
      onListsChanged();

      // Queue the service pages with the capability tags.
      const queued = await onIndexList(urls, { role: svcRole, proto: svcProtos[0], followLinks: false });

      if (published.delivered > 0) {
        await saveListing({
          d: input.id,
          name: input.name,
          role: svcRole,
          protocols: svcProtos,
          price: svcPrice,
          ...(svcEndpoint.trim() ? { endpoint: svcEndpoint.trim() } : {}),
          ...(svcHomepage.trim() ? { homepage: svcHomepage.trim() } : {}),
          publishedAt: Date.now(),
          eventId: published.eventId,
          pubkey: published.coordinate.split(':')[1] ?? '',
          status: 'active',
        });
        await reloadListings();
        setSvcStatus({
          kind: 'ok',
          text: `Listing published to ${published.delivered} relay${published.delivered === 1 ? '' : 's'} — ${published.coordinate}. ${queued.added.toLocaleString()} service page${queued.added === 1 ? '' : 's'} queued (saved as "${list.name}").`,
        });
        setSvcName('');
        setSvcSummary('');
        setSvcUrls('');
        setSvcEndpoint('');
        setSvcHomepage('');
        setSvcTags('');
        setSvcAbout('');
      } else {
        setSvcStatus({
          kind: 'warn',
          text: `No relay accepted the listing yet — the service pages were queued (saved as "${list.name}"), but press Publish again to retry the announcement.`,
        });
      }
    } catch (error) {
      setSvcStatus({ kind: 'err', text: error instanceof Error ? error.message : 'Publishing failed' });
    } finally {
      setSvcBusy(false);
    }
  };

  const handleWithdraw = async (listing: PublishedListing) => {
    setListingStatus(null);
    try {
      const result = await onWithdrawCapability(listing.d);
      if (result && result.delivered > 0) {
        await markListingWithdrawn(listing.d);
        await reloadListings();
        setListingStatus({ kind: 'ok', text: `Withdrawn "${listing.name}" (${result.delivered} relay${result.delivered === 1 ? '' : 's'}).` });
      } else {
        setListingStatus({ kind: 'warn', text: 'No relay accepted the withdrawal — try again.' });
      }
    } catch (error) {
      setListingStatus({ kind: 'err', text: error instanceof Error ? error.message : 'Withdrawal failed' });
    }
  };

  /* ------------------------------------------------------------------ */
  /* Render                                                              */
  /* ------------------------------------------------------------------ */

  return (
    <div className="space-y-6">
      {/* ---------------- Add links ---------------- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Link2 className="h-4 w-4 text-primary" />
            Add links
          </CardTitle>
          <CardDescription>
            Paste a list of URLs or upload a file (.txt, .csv, .json, .md — any text with links in it).
            Lists are saved on this device and indexed only when you press Index now.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="list-name">List name</Label>
            <Input
              id="list-name"
              placeholder="e.g. My bookmarks, Awesome tools, Feeds to index"
              value={addName}
              onChange={(e) => setAddName(e.target.value)}
              maxLength={80}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="list-text">Paste links</Label>
            <Textarea
              id="list-text"
              placeholder={'https://example.com\nhttps://another.example/page\n# lines starting with # are skipped'}
              value={addText}
              onChange={(e) => {
                setAddText(e.target.value);
                setAddStatus(null);
              }}
              rows={5}
              className="font-mono text-xs"
              disabled={addFile !== null}
            />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <input
              ref={fileInputRef}
              type="file"
              accept=".txt,.csv,.json,.md,.tsv,text/*"
              className="hidden"
              onChange={(e) => void handleFilePick(e.target.files?.[0] ?? null)}
            />
            <Button variant="outline" size="sm" onClick={() => fileInputRef.current?.click()}>
              <Upload className="h-3.5 w-3.5 mr-1.5" />
              Upload a file
            </Button>
            {addFile && (
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                <FileText className="h-3.5 w-3.5" />
                {addFile.name}
                {addFileParsed && ` — ${addFileParsed.urls.toLocaleString()} URLs`}
                <button
                  type="button"
                  className="text-muted-foreground hover:text-foreground"
                  onClick={() => void handleFilePick(null)}
                  aria-label="Remove file"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </span>
            )}
          </div>

          {textPreview && addText.trim() && (
            <p className="text-xs text-muted-foreground">
              {textPreview.urls.length.toLocaleString()} URLs detected
              {textPreview.invalid > 0 && ` · ${textPreview.invalid} invalid skipped`}
              {textPreview.truncated && ' · truncated to 50,000'}
            </p>
          )}

          <div className="flex items-center gap-3">
            <Button
              onClick={() => void handleSaveList()}
              disabled={addBusy || (!addText.trim() && !addFile)}
            >
              {addBusy ? <Loader2 className="h-4 w-4 mr-1.5 motion-safe:animate-spin" /> : <Upload className="h-4 w-4 mr-1.5" />}
              Save list
            </Button>
          </div>

          <StatusLine status={addStatus} />
        </CardContent>
      </Card>

      {/* ---------------- Your lists ---------------- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Globe className="h-4 w-4 text-primary" />
            Your lists
          </CardTitle>
          <CardDescription>
            Index a list to queue its URLs. The list IS the crawl plan — links are not followed
            from its pages unless you enable it below.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Switch id="follow-links" checked={followLinks} onCheckedChange={setFollowLinks} />
              <Label htmlFor="follow-links" className="text-xs text-muted-foreground">
                Also follow links, feeds &amp; sitemaps from these pages
              </Label>
            </div>
          </div>

          {lists.length === 0 ? (
            <div className="py-8 text-center text-muted-foreground">
              <Link2 className="h-10 w-10 mx-auto mb-3 opacity-20" />
              <p className="text-sm">No lists yet.</p>
              <p className="text-xs mt-1">Paste links or upload a file above — your lists stay on this device.</p>
            </div>
          ) : (
            <div className="space-y-3">
              {lists.map((list) => {
                const progress = indexing[list.id];
                return (
                  <div key={list.id} className="rounded-lg border p-3 space-y-2">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-medium text-sm truncate">{list.name}</p>
                        <div className="flex flex-wrap items-center gap-1.5 mt-1">
                          <Badge variant="outline" className="text-xs">
                            {list.urls.length.toLocaleString()} URLs
                          </Badge>
                          <Badge variant="secondary" className="text-xs">
                            {list.source === 'file' ? 'uploaded' : 'pasted'}
                          </Badge>
                          {list.role && (
                            <Badge variant="outline" className="text-xs border-primary/40 text-primary">
                              {list.role}
                            </Badge>
                          )}
                          {list.proto && (
                            <Badge variant="outline" className="text-xs border-primary/40 text-primary">
                              {list.proto}
                            </Badge>
                          )}
                          <span className="text-xs text-muted-foreground">
                            {new Date(list.createdAt).toLocaleDateString()}
                          </span>
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={!ready || !!progress}
                          onClick={() => void indexList(list)}
                        >
                          {progress ? (
                            <Loader2 className="h-3.5 w-3.5 mr-1.5 motion-safe:animate-spin" />
                          ) : (
                            <Rocket className="h-3.5 w-3.5 mr-1.5" />
                          )}
                          Index now
                        </Button>
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button size="sm" variant="ghost" className="text-destructive">
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Delete "{list.name}"?</AlertDialogTitle>
                              <AlertDialogDescription>
                                Removes the list from this device. Already-crawled pages and queued
                                URLs are not affected.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Cancel</AlertDialogCancel>
                              <AlertDialogAction onClick={() => void handleDeleteList(list)}>
                                Delete
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      </div>
                    </div>
                    {progress && (
                      <div className="space-y-1">
                        <Progress
                          value={progress.total > 0 ? Math.round((progress.done / progress.total) * 100) : 0}
                          className="h-1.5"
                        />
                        <p className="text-xs text-muted-foreground">
                          Queueing {progress.done.toLocaleString()} / {progress.total.toLocaleString()}…
                        </p>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          <StatusLine status={indexStatus} />

          {indexStatus?.kind === 'ok' && !isRunning && (
            <div className="flex items-center justify-between rounded-lg border border-primary/40 bg-primary/5 px-4 py-3">
              <p className="text-sm">
                <span className="font-medium">URLs queued.</span>{' '}
                <span className="text-muted-foreground">Start the crawler to begin publishing observations.</span>
              </p>
              <Button size="sm" className="gap-1.5 shrink-0" onClick={onStart} disabled={!ready}>
                <Play className="h-3.5 w-3.5" />
                Start Crawling
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ---------------- Submit your service ---------------- */}
      <Card className="border-primary/30">
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Send className="h-4 w-4 text-primary" />
            Submit your service
          </CardTitle>
          <CardDescription>
            Publish a kind 39698 capability announcement so agents can discover your service — and
            index its pages with role/proto tags. Signed by this device's indexer identity.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="svc-name">Service name *</Label>
              <Input
                id="svc-name"
                placeholder="e.g. Echo Tool"
                value={svcName}
                onChange={(e) => setSvcName(e.target.value)}
                maxLength={80}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="svc-summary">One-line summary *</Label>
              <Input
                id="svc-summary"
                placeholder="e.g. Returns text. Free."
                value={svcSummary}
                onChange={(e) => setSvcSummary(e.target.value)}
                maxLength={240}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="svc-urls">Service pages to index * (one URL per line)</Label>
            <Textarea
              id="svc-urls"
              placeholder={'https://myservice.example\nhttps://myservice.example/docs'}
              value={svcUrls}
              onChange={(e) => setSvcUrls(e.target.value)}
              rows={3}
              className="font-mono text-xs"
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="space-y-2">
              <Label>Role *</Label>
              <Select value={svcRole} onValueChange={(v) => setSvcRole(v as Role)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ROLES.map((role) => (
                    <SelectItem key={role} value={role}>
                      {role}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Price *</Label>
              <Select value={svcPrice} onValueChange={(v) => setSvcPrice(v as Price)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PRICES.map((price) => (
                    <SelectItem key={price} value={price}>
                      {price}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Protocols *</Label>
              <div className="flex flex-wrap gap-1.5">
                {PROTOS.map((proto) => (
                  <Button
                    key={proto}
                    type="button"
                    size="sm"
                    variant={svcProtos.includes(proto) ? 'default' : 'outline'}
                    className="h-7 px-2 text-xs"
                    onClick={() => toggleProto(proto)}
                  >
                    {proto}
                  </Button>
                ))}
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="svc-endpoint">Endpoint (optional)</Label>
              <Input
                id="svc-endpoint"
                placeholder="https://… or wss://…"
                value={svcEndpoint}
                onChange={(e) => setSvcEndpoint(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="svc-homepage">Homepage (optional)</Label>
              <Input
                id="svc-homepage"
                placeholder="https://…"
                value={svcHomepage}
                onChange={(e) => setSvcHomepage(e.target.value)}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="svc-tags">Capability tags (optional)</Label>
              <Input
                id="svc-tags"
                placeholder="e.g. echo, text, summarize"
                value={svcTags}
                onChange={(e) => setSvcTags(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="svc-about">About (optional)</Label>
              <Input
                id="svc-about"
                placeholder="Longer description"
                value={svcAbout}
                onChange={(e) => setSvcAbout(e.target.value)}
                maxLength={2000}
              />
            </div>
          </div>

          <div className="flex items-center gap-3">
            <Button onClick={() => void handleSubmitService()} disabled={svcBusy || !ready}>
              {svcBusy ? (
                <Loader2 className="h-4 w-4 mr-1.5 motion-safe:animate-spin" />
              ) : (
                <Send className="h-4 w-4 mr-1.5" />
              )}
              Publish listing &amp; index
            </Button>
            <p className="text-xs text-muted-foreground">
              Discovery is not invocation — the listing is a public pointer, nothing runs automatically.
            </p>
          </div>

          <StatusLine status={svcStatus} />
        </CardContent>
      </Card>

      {/* ---------------- Your listings ---------------- */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-primary" />
            Your listings
          </CardTitle>
          <CardDescription>
            Capability announcements published from this device. Withdraw replaces the listing with
            a "withdrawn" status so it drops out of agent searches.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {listings.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">
              No listings published yet.
            </p>
          ) : (
            listings.map((listing) => (
              <div key={listing.d} className="rounded-lg border p-3 space-y-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium text-sm truncate">{listing.name}</p>
                    <p className="text-xs text-muted-foreground font-mono truncate">
                      39698:{listing.pubkey.slice(0, 12)}…:{listing.d}
                    </p>
                    <div className="flex flex-wrap items-center gap-1.5 mt-1">
                      <Badge variant="outline" className="text-xs">
                        {listing.role}
                      </Badge>
                      {listing.protocols.map((proto) => (
                        <Badge key={proto} variant="secondary" className="text-xs">
                          {proto}
                        </Badge>
                      ))}
                      <Badge variant="outline" className="text-xs">
                        {listing.price}
                      </Badge>
                      <span className="text-xs text-muted-foreground">
                        {new Date(listing.publishedAt).toLocaleString()}
                      </span>
                    </div>
                  </div>
                  <div className="shrink-0">
                    {listing.status === 'active' ? (
                      <Button size="sm" variant="outline" className="text-destructive" onClick={() => void handleWithdraw(listing)}>
                        <Ban className="h-3.5 w-3.5 mr-1.5" />
                        Withdraw
                      </Button>
                    ) : (
                      <Badge variant="secondary" className="text-xs">
                        withdrawn
                      </Badge>
                    )}
                  </div>
                </div>
              </div>
            ))
          )}
          <StatusLine status={listingStatus} />
        </CardContent>
      </Card>

      <Separator />

      <p className="text-xs text-muted-foreground leading-relaxed text-center">
        Lists and listings are stored on this device only. Indexing publishes public SIP-01
        observations (kind 39697) for each URL; service submissions additionally publish a kind
        39698 capability announcement. Nothing runs without pressing Start.
      </p>
    </div>
  );
}
