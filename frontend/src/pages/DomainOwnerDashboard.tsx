import { useEffect, useState } from 'react';
import { UserProfile } from '../keycloak';
import {
    domainOwnerApi, FilterOptions, DiscoveryResult, TrialFilter,
    Collection, PendingRequest, CollectionGrant, DashboardStats,
    PublishCollectionInput, GovernancePurpose
} from '../api/client';
import StatusBadge from '../components/StatusBadge';
import {
    Database, Clock, Shield, Building2, Plus, Search,
    CheckCircle, XCircle, AlertTriangle, Eye, RefreshCw,
    Filter, Package, ChevronRight, Zap
} from 'lucide-react';

interface Props { user: UserProfile }

type Tab = 'overview' | 'publish' | 'requests' | 'grants' | 'governance';

const FILTER_LABELS: Record<string, string> = {
    therapeutic_areas: 'Therapeutic Areas',
    phases: 'Phases',
    study_types: 'Study Types',
    regions: 'Regions',
    region: 'Regions',
    countries: 'Countries',
    country: 'Country',
    overall_statuses: 'Statuses',
    lead_sponsors: 'Lead Sponsors',
    min_enrollment: 'Min Enrollment',
    approved_purposes: 'Purposes',
    purposes: 'Purposes',
    purpose: 'Purpose',
    minimum_cohort_size: 'Min Cohort Size',
    resource_classification: 'Classification',
};

function extractRequestedPurposes(scope?: Record<string, unknown>): string[] {
    if (!scope) return [];
    const raw =
        scope.approved_purposes
        ?? scope.purposes
        ?? scope.purpose;
    if (Array.isArray(raw)) {
        return raw.map(v => String(v).trim()).filter(Boolean);
    }
    if (typeof raw === 'string' && raw.trim()) {
        return [raw.trim()];
    }
    return [];
}

function buildFilterTags(criteria?: Record<string, unknown>): string[] {
    if (!criteria) return [];
    return Object.entries(criteria).flatMap(([key, value]) => {
        if (value === null || value === undefined) return [];
        const displayKey = FILTER_LABELS[key]
            ?? key
                .split('_')
                .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
                .join(' ');

        if (Array.isArray(value)) {
            const cleaned = value.map((v) => String(v).trim()).filter(Boolean);
            if (cleaned.length === 0) return [];
            return [`${displayKey}: ${cleaned.join(', ')}`];
        }
        if (typeof value === 'boolean') {
            return [`${displayKey}: ${value ? 'Yes' : 'No'}`];
        }
        if (typeof value === 'string' || typeof value === 'number') {
            const text = String(value).trim();
            if (!text) return [];
            return [`${displayKey}: ${text}`];
        }
        return [];
    });
}

export default function DomainOwnerDashboard({ user }: Props) {
    const [tab, setTab] = useState<Tab>('overview');
    const [stats, setStats] = useState<DashboardStats | null>(null);
    const [collections, setCollections] = useState<Collection[]>([]);
    const [pending, setPending] = useState<PendingRequest[]>([]);
    const [grants, setGrants] = useState<CollectionGrant[]>([]);
    const [purposes, setPurposes] = useState<GovernancePurpose[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');

    // Publish wizard state
    const [filterOptions, setFilterOptions] = useState<FilterOptions | null>(null);
    const [filters, setFilters] = useState<TrialFilter>({});
    const [discovery, setDiscovery] = useState<DiscoveryResult | null>(null);
    const [discovering, setDiscovering] = useState(false);
    const [publishForm, setPublishForm] = useState({ name: '', description: '', sensitivity: 'standard', dynamic: true });
    const [publishing, setPublishing] = useState(false);

    // Review modal
    const [reviewing, setReviewing] = useState<PendingRequest | null>(null);
    const [reviewNotes, setReviewNotes] = useState('');
    const [grantDays, setGrantDays] = useState(365);

    // Governance purpose form
    const [purposeForm, setPurposeForm] = useState({ purpose_key: '', label: '', description: '' });
    const [savingPurpose, setSavingPurpose] = useState(false);

    useEffect(() => { loadAll(); }, []);

    async function loadAll() {
        setLoading(true);
        try {
            const [s, c, p, g, gp] = await Promise.all([
                domainOwnerApi.getStats(),
                domainOwnerApi.listCollections(),
                domainOwnerApi.listPendingRequests(),
                domainOwnerApi.listGrants(),
                domainOwnerApi.listGovernancePurposes(),
            ]);
            setStats(s);
            setCollections(c.collections);
            setPending(p.requests);
            setGrants(g.grants);
            setPurposes(gp.purposes);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Load failed');
        } finally {
            setLoading(false);
        }
    }

    async function loadFilterOptions() {
        if (filterOptions) return;
        try {
            const opts = await domainOwnerApi.getFilterOptions();
            setFilterOptions(opts);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Failed to load filter options');
        }
    }

    async function handleDiscover() {
        setDiscovering(true);
        setError('');
        try {
            const result = await domainOwnerApi.discoverTrials(filters);
            setDiscovery(result);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Discovery failed');
        } finally {
            setDiscovering(false);
        }
    }

    async function handlePublish() {
        if (!publishForm.name || !publishForm.description) return;
        setPublishing(true);
        try {
            await domainOwnerApi.publishCollection({
                name: publishForm.name,
                description: publishForm.description,
                filter_criteria: filters,
                sensitivity_level: publishForm.sensitivity,
                is_dynamic: publishForm.dynamic,
            });
            setTab('overview');
            setDiscovery(null);
            setFilters({});
            setPublishForm({ name: '', description: '', sensitivity: 'standard', dynamic: true });
            loadAll();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Publish failed');
        } finally {
            setPublishing(false);
        }
    }

    async function handleReview(action: 'approve' | 'reject') {
        if (!reviewing) return;
        try {
            await domainOwnerApi.reviewRequest(reviewing.request_id, {
                action, notes: reviewNotes, grant_duration_days: grantDays,
            });
            setReviewing(null);
            setReviewNotes('');
            loadAll();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Review failed');
        }
    }

    async function handleRefresh() {
        try {
            await domainOwnerApi.refreshCollections();
            loadAll();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Refresh failed');
        }
    }

    async function handleCreatePurpose() {
        if (!purposeForm.purpose_key.trim() || !purposeForm.label.trim()) return;
        setSavingPurpose(true);
        try {
            await domainOwnerApi.createGovernancePurpose({
                purpose_key: purposeForm.purpose_key.trim(),
                label: purposeForm.label.trim(),
                description: purposeForm.description.trim() || undefined,
            });
            setPurposeForm({ purpose_key: '', label: '', description: '' });
            const gp = await domainOwnerApi.listGovernancePurposes();
            setPurposes(gp.purposes);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Failed to create purpose');
        } finally {
            setSavingPurpose(false);
        }
    }

    async function handleDeactivatePurpose(purposeKey: string) {
        try {
            await domainOwnerApi.deactivateGovernancePurpose(purposeKey);
            const gp = await domainOwnerApi.listGovernancePurposes();
            setPurposes(gp.purposes);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Failed to deactivate purpose');
        }
    }

    if (loading) {
        return <div className="flex justify-center py-20"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600" /></div>;
    }

    const tabs: { key: Tab; label: string; count?: number }[] = [
        { key: 'overview', label: 'Overview' },
        { key: 'publish', label: 'Publish Collection' },
        { key: 'requests', label: 'Pending Requests', count: pending.length },
        { key: 'grants', label: 'Active Grants', count: grants.length },
        { key: 'governance', label: 'Governance Purposes', count: purposes.length },
    ];

    return (
        <div>
            <div className="flex justify-between items-start mb-6">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Domain Owner Dashboard</h1>
                    <p className="text-sm text-gray-500 mt-1">Publish trial collections and manage organization access</p>
                </div>
                <button onClick={handleRefresh} className="flex items-center space-x-2 px-3 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg">
                    <RefreshCw className="h-4 w-4" /><span>Refresh Dynamic</span>
                </button>
            </div>

            {error && (
                <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded-lg flex items-center justify-between">
                    <span className="text-red-700 text-sm">{error}</span>
                    <button onClick={() => setError('')} className="text-red-500">&times;</button>
                </div>
            )}

            {/* Stats */}
            <div className="grid grid-cols-4 gap-4 mb-6">
                <div className="bg-white rounded-lg shadow p-5 border-l-4 border-indigo-500">
                    <p className="text-xs text-gray-500 uppercase">Total Trials</p>
                    <p className="text-2xl font-bold">{stats?.total_trials_in_system}</p>
                    <p className="text-xs text-gray-400">{stats?.unpublished_trials} unpublished</p>
                </div>
                <div className="bg-white rounded-lg shadow p-5 border-l-4 border-purple-500">
                    <p className="text-xs text-gray-500 uppercase">Collections</p>
                    <p className="text-2xl font-bold">{stats?.collections}</p>
                    <p className="text-xs text-gray-400">{stats?.published_trials} trials published</p>
                </div>
                <div className="bg-white rounded-lg shadow p-5 border-l-4 border-yellow-500">
                    <p className="text-xs text-gray-500 uppercase">Pending Requests</p>
                    <p className="text-2xl font-bold">{stats?.pending_requests}</p>
                </div>
                <div className="bg-white rounded-lg shadow p-5 border-l-4 border-green-500">
                    <p className="text-xs text-gray-500 uppercase">Orgs with Access</p>
                    <p className="text-2xl font-bold">{stats?.organizations_with_access}</p>
                </div>
            </div>

            {/* Tabs */}
            <div className="border-b border-gray-200 mb-6">
                <nav className="flex space-x-6">
                    {tabs.map(t => (
                        <button key={t.key} onClick={() => { setTab(t.key); if (t.key === 'publish') loadFilterOptions(); }}
                            className={`py-3 text-sm font-medium border-b-2 ${tab === t.key ? 'border-indigo-500 text-indigo-600' : 'border-transparent text-gray-500 hover:text-gray-700'
                                }`}>
                            {t.label}
                            {t.count !== undefined && t.count > 0 && (
                                <span className="ml-2 bg-yellow-100 text-yellow-700 px-2 py-0.5 rounded-full text-xs">{t.count}</span>
                            )}
                        </button>
                    ))}
                </nav>
            </div>

            {/* ─── OVERVIEW TAB ──────────────────────────────────── */}
            {tab === 'overview' && (
                <div className="space-y-4">
                    {collections.length === 0 ? (
                        <div className="bg-white rounded-lg shadow p-12 text-center">
                            <Package className="h-12 w-12 text-gray-300 mx-auto mb-4" />
                            <h3 className="text-lg font-medium text-gray-900">No Collections Published</h3>
                            <p className="text-sm text-gray-500 mt-2">Create your first collection to make trial data available in the marketplace</p>
                            <button onClick={() => { setTab('publish'); loadFilterOptions(); }} className="mt-4 px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm">
                                <Plus className="h-4 w-4 inline mr-1" /> Publish Collection
                            </button>
                        </div>
                    ) : (
                        collections.map(c => (
                            <div key={c.collection_id} className="bg-white rounded-lg shadow p-6">
                                <div className="flex justify-between items-start">
                                    <div>
                                        <div className="flex items-center space-x-3">
                                            <h3 className="text-base font-semibold text-gray-900">{c.name}</h3>
                                            {c.is_dynamic && <span className="flex items-center text-xs text-purple-600 bg-purple-50 px-2 py-0.5 rounded-full"><Zap className="h-3 w-3 mr-1" />Dynamic</span>}
                                            <StatusBadge status={c.sensitivity_level} />
                                            <span className="text-xs bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded-full">
                                                {buildFilterTags(c.filter_criteria as Record<string, unknown>).length} filter{buildFilterTags(c.filter_criteria as Record<string, unknown>).length !== 1 ? 's' : ''}
                                            </span>
                                        </div>
                                        <p className="text-sm text-gray-500 mt-1">{c.description}</p>
                                        <div className="flex flex-wrap gap-2 mt-3">
                                            {c.therapeutic_areas?.map(a => <span key={a} className="text-xs bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded">{a}</span>)}
                                            {c.phases?.map(p => <span key={p} className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded">{p}</span>)}
                                        </div>
                                        <div className="mt-3">
                                            <p className="text-xs font-medium text-gray-600 mb-1">Filters Applied</p>
                                            <div className="flex flex-wrap gap-1.5">
                                                {buildFilterTags(c.filter_criteria as Record<string, unknown>).length > 0 ? (
                                                    buildFilterTags(c.filter_criteria as Record<string, unknown>).map((tag) => (
                                                        <span key={`${c.collection_id}-${tag}`} className="text-xs bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded border border-emerald-100">
                                                            {tag}
                                                        </span>
                                                    ))
                                                ) : (
                                                    <span className="text-xs text-gray-500">No filters configured</span>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                    <div className="text-right text-sm text-gray-500">
                                        <p><strong>{c.trial_count}</strong> trials · <strong>{c.total_patients}</strong> patients</p>
                                        <p className="mt-1">{c.organizations_with_access} org(s) with access</p>
                                        {c.pending_requests > 0 && <p className="text-yellow-600 font-medium">{c.pending_requests} pending</p>}
                                    </div>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            )}

            {/* ─── PUBLISH TAB (Wizard) ──────────────────────────── */}
            {tab === 'publish' && filterOptions && (
                <div className="grid grid-cols-3 gap-6">
                    {/* Left: Filters */}
                    <div className="col-span-2 space-y-6">
                        <div className="bg-white rounded-lg shadow p-6">
                            <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-4">
                                <Filter className="h-4 w-4 inline mr-2" />Define Collection Criteria
                            </h2>
                            <div className="grid grid-cols-2 gap-4">
                                {/* Therapeutic Area */}
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Therapeutic Area</label>
                                    <div className="flex flex-wrap gap-2">
                                        {filterOptions.therapeutic_areas.map(a => (
                                            <button key={a} onClick={() => setFilters(f => ({
                                                ...f, therapeutic_areas: f.therapeutic_areas?.includes(a)
                                                    ? f.therapeutic_areas.filter(x => x !== a) : [...(f.therapeutic_areas || []), a]
                                            }))} className={`px-3 py-1 rounded-full text-xs font-medium transition ${filters.therapeutic_areas?.includes(a) ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                                }`}>{a}</button>
                                        ))}
                                    </div>
                                </div>
                                {/* Phase */}
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Phase</label>
                                    <div className="flex flex-wrap gap-2">
                                        {filterOptions.phases.map(p => (
                                            <button key={p} onClick={() => setFilters(f => ({
                                                ...f, phases: f.phases?.includes(p) ? f.phases.filter(x => x !== p) : [...(f.phases || []), p]
                                            }))} className={`px-3 py-1 rounded-full text-xs font-medium transition ${filters.phases?.includes(p) ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                                }`}>{p}</button>
                                        ))}
                                    </div>
                                </div>
                                {/* Study Type */}
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Study Type</label>
                                    <div className="flex flex-wrap gap-2">
                                        {filterOptions.study_types.map(s => (
                                            <button key={s} onClick={() => setFilters(f => ({
                                                ...f, study_types: f.study_types?.includes(s) ? f.study_types.filter(x => x !== s) : [...(f.study_types || []), s]
                                            }))} className={`px-3 py-1 rounded-full text-xs font-medium transition ${filters.study_types?.includes(s) ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                                }`}>{s}</button>
                                        ))}
                                    </div>
                                </div>
                                {/* Regions */}
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Regions</label>
                                    <div className="flex flex-wrap gap-2">
                                        {filterOptions.regions.map(r => (
                                            <button key={r} onClick={() => setFilters(f => ({
                                                ...f, regions: f.regions?.includes(r) ? f.regions.filter(x => x !== r) : [...(f.regions || []), r]
                                            }))} className={`px-3 py-1 rounded-full text-xs font-medium transition ${filters.regions?.includes(r) ? 'bg-indigo-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                                }`}>{r}</button>
                                        ))}
                                    </div>
                                </div>
                                {/* Min Enrollment */}
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Min Enrollment</label>
                                    <input type="number" value={filters.min_enrollment ?? ''} onChange={e => setFilters(f => ({
                                        ...f, min_enrollment: e.target.value ? Number(e.target.value) : undefined
                                    }))} className="w-32 border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="e.g. 50" />
                                </div>
                            </div>
                            <button onClick={handleDiscover} disabled={discovering}
                                className="mt-6 flex items-center space-x-2 px-4 py-2 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 text-sm font-medium disabled:opacity-50">
                                <Search className="h-4 w-4" />
                                <span>{discovering ? 'Searching...' : 'Preview Matching Trials'}</span>
                            </button>
                        </div>

                        {/* Discovery results */}
                        {discovery && (
                            <div className="bg-white rounded-lg shadow p-6">
                                <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-4">
                                    <Eye className="h-4 w-4 inline mr-2" />
                                    {discovery.total_matching} Trials Found
                                    <span className="text-xs font-normal text-gray-500 ml-2">
                                        ({discovery.available_to_publish} new, {discovery.already_published} already published)
                                    </span>
                                </h2>
                                <div className="max-h-80 overflow-y-auto divide-y divide-gray-100">
                                    {discovery.trials.map(t => (
                                        <div key={t.trial_id} className={`py-3 flex justify-between items-center ${t.already_published ? 'opacity-50' : ''}`}>
                                            <div>
                                                <p className="text-sm font-medium text-gray-900">{t.title || t.nct_id}</p>
                                                <div className="flex items-center gap-2 mt-1">
                                                    <span className="text-xs text-gray-500">{t.nct_id}</span>
                                                    <span className="text-xs text-gray-400">{t.phase}</span>
                                                    <span className="text-xs text-gray-400">{t.therapeutic_area}</span>
                                                    <span className="text-xs text-gray-400">{t.patient_count}p</span>
                                                </div>
                                            </div>
                                            {t.already_published && <span className="text-xs text-gray-400">Already published</span>}
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Right: Publish form */}
                    <div className="space-y-6">
                        <div className="bg-white rounded-lg shadow p-6 sticky top-24">
                            <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-4">Collection Details</h2>
                            <div className="space-y-4">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                                    <input type="text" value={publishForm.name} onChange={e => setPublishForm(f => ({ ...f, name: e.target.value }))}
                                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="e.g. Phase III Oncology Trials" />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                                    <textarea rows={3} value={publishForm.description} onChange={e => setPublishForm(f => ({ ...f, description: e.target.value }))}
                                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" placeholder="Describe what this collection contains..." />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Sensitivity</label>
                                    <select value={publishForm.sensitivity} onChange={e => setPublishForm(f => ({ ...f, sensitivity: e.target.value }))}
                                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm">
                                        <option value="public">Public</option>
                                        <option value="standard">Standard</option>
                                        <option value="sensitive">Sensitive</option>
                                        <option value="restricted">Restricted</option>
                                    </select>
                                </div>
                                <label className="flex items-center space-x-2 cursor-pointer">
                                    <input type="checkbox" checked={publishForm.dynamic} onChange={e => setPublishForm(f => ({ ...f, dynamic: e.target.checked }))}
                                        className="h-4 w-4 text-indigo-600 rounded" />
                                    <span className="text-sm text-gray-700">Dynamic (auto-include new matching trials)</span>
                                </label>

                                {discovery && (
                                    <div className="p-3 bg-indigo-50 rounded-lg">
                                        <p className="text-sm font-medium text-indigo-900">{discovery.total_matching} trials</p>
                                        <p className="text-xs text-indigo-700">{discovery.summary.total_patients} patients · {discovery.summary.therapeutic_areas.join(', ')}</p>
                                    </div>
                                )}

                                <button onClick={handlePublish}
                                    disabled={publishing || !publishForm.name || !publishForm.description || !discovery || discovery.total_matching === 0}
                                    className="w-full flex items-center justify-center space-x-2 py-2.5 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 text-sm font-medium disabled:opacity-50">
                                    <Package className="h-4 w-4" />
                                    <span>{publishing ? 'Publishing...' : 'Publish Collection'}</span>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* ─── REQUESTS TAB ──────────────────────────────────── */}
            {tab === 'requests' && (
                <div className="space-y-4">
                    {pending.length === 0 ? (
                        <div className="bg-white rounded-lg shadow p-12 text-center">
                            <CheckCircle className="h-12 w-12 text-gray-300 mx-auto mb-4" />
                            <p className="text-gray-500">No pending requests</p>
                        </div>
                    ) : (
                        pending.map(req => (
                            <div key={req.request_id} className="bg-white rounded-lg shadow p-6">
                                <div className="flex justify-between items-start">
                                    <div>
                                        <div className="flex items-center space-x-3">
                                            <h3 className="font-semibold text-gray-900">{req.collection_name}</h3>
                                            <StatusBadge status="pending" />
                                        </div>
                                        <p className="text-sm text-gray-500 mt-1">
                                            <Building2 className="h-4 w-4 inline mr-1" />{req.requesting_org_id}
                                            <span className="mx-2">·</span>{req.trial_count} trials
                                            <span className="mx-2">·</span>{req.total_patients} patients
                                        </p>
                                        <div className="mt-3 p-3 bg-gray-50 rounded-lg">
                                            <p className="text-sm text-gray-700"><strong>Justification:</strong> {req.justification}</p>
                                            {extractRequestedPurposes(req.scope).length > 0 && (
                                                <div className="mt-2">
                                                    <p className="text-xs font-medium text-gray-600 mb-1">Requested Purpose(s)</p>
                                                    <div className="flex flex-wrap gap-1.5">
                                                        {extractRequestedPurposes(req.scope).map(p => (
                                                            <span key={p} className="text-xs bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded">
                                                                {p}
                                                            </span>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                        <div className="flex flex-wrap gap-2 mt-2">
                                            {req.therapeutic_areas?.map(a => <span key={a} className="text-xs bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded">{a}</span>)}
                                        </div>
                                    </div>
                                    <button onClick={() => setReviewing(req)} className="px-4 py-2 bg-indigo-50 text-indigo-700 rounded-lg hover:bg-indigo-100 text-sm font-medium">
                                        Review
                                    </button>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            )}

            {/* ─── GRANTS TAB ────────────────────────────────────── */}
            {tab === 'grants' && (
                <div className="space-y-4">
                    {grants.length === 0 ? (
                        <div className="bg-white rounded-lg shadow p-12 text-center">
                            <Shield className="h-12 w-12 text-gray-300 mx-auto mb-4" />
                            <p className="text-gray-500">No active grants</p>
                        </div>
                    ) : (
                        grants.map(g => (
                            <div key={`${g.collection_id}-${g.organization_id}`} className="bg-white rounded-lg shadow p-6 flex justify-between items-start gap-6">
                                <div>
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <h3 className="font-medium text-gray-900">{g.collection_name}</h3>
                                        <span className="text-xs bg-emerald-100 text-emerald-800 px-2 py-0.5 rounded-full">
                                            {buildFilterTags(g.filter_criteria as Record<string, unknown> | undefined).length} filter{buildFilterTags(g.filter_criteria as Record<string, unknown> | undefined).length !== 1 ? 's' : ''}
                                        </span>
                                    </div>
                                    <p className="text-sm text-gray-500">
                                        <Building2 className="h-4 w-4 inline mr-1" />{g.organization_id}
                                        <span className="mx-2">·</span>{g.trial_count} trials
                                        <span className="mx-2">·</span>Expires {new Date(g.earliest_expiry).toLocaleDateString()}
                                    </p>
                                    <div className="mt-2">
                                        <p className="text-xs font-medium text-gray-600 mb-1">Collection Filters</p>
                                        <div className="flex flex-wrap gap-1.5">
                                            {buildFilterTags(g.filter_criteria as Record<string, unknown> | undefined).length > 0 ? (
                                                buildFilterTags(g.filter_criteria as Record<string, unknown> | undefined).map((tag) => (
                                                    <span key={`${g.collection_id}-${g.organization_id}-${tag}`} className="text-xs bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded border border-emerald-100">
                                                        {tag}
                                                    </span>
                                                ))
                                            ) : (
                                                <span className="text-xs text-gray-500">No filters configured</span>
                                            )}
                                        </div>
                                    </div>
                                </div>
                                <button onClick={() => {
                                    const reason = prompt('Reason for revocation:');
                                    if (reason) domainOwnerApi.revokeGrant(g.collection_id, g.organization_id, reason).then(loadAll);
                                }} className="px-3 py-1 text-sm text-red-600 hover:bg-red-50 rounded border border-red-200">
                                    Revoke
                                </button>
                            </div>
                        ))
                    )}
                </div>
            )}

            {/* ─── GOVERNANCE TAB ───────────────────────────────── */}
            {tab === 'governance' && (
                <div className="grid grid-cols-3 gap-6">
                    <div className="col-span-2 space-y-4">
                        <div className="bg-white rounded-lg shadow p-6">
                            <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-4">Active Purposes</h2>
                            {purposes.length === 0 ? (
                                <p className="text-sm text-gray-500">No purposes configured.</p>
                            ) : (
                                <div className="space-y-3">
                                    {purposes.map(p => (
                                        <div key={`${p.owner_id || 'global'}-${p.purpose_key}`} className="border border-gray-200 rounded-lg p-4 flex justify-between items-start">
                                            <div>
                                                <div className="flex items-center gap-2">
                                                    <p className="text-sm font-semibold text-gray-900">{p.label}</p>
                                                    <span className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded">{p.purpose_key}</span>
                                                    <span className={`text-xs px-2 py-0.5 rounded ${p.owner_id ? 'bg-indigo-50 text-indigo-700' : 'bg-emerald-50 text-emerald-700'}`}>
                                                        {p.owner_id ? 'owner-defined' : 'global'}
                                                    </span>
                                                </div>
                                                {p.description && <p className="text-sm text-gray-500 mt-1">{p.description}</p>}
                                            </div>
                                            {p.owner_id && (
                                                <button
                                                    onClick={() => handleDeactivatePurpose(p.purpose_key)}
                                                    className="px-3 py-1 text-xs text-red-600 hover:bg-red-50 rounded border border-red-200"
                                                >
                                                    Deactivate
                                                </button>
                                            )}
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>

                    <div>
                        <div className="bg-white rounded-lg shadow p-6 sticky top-24">
                            <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-4">Create Purpose</h2>
                            <div className="space-y-3">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Purpose Key</label>
                                    <input
                                        type="text"
                                        value={purposeForm.purpose_key}
                                        onChange={e => setPurposeForm(f => ({ ...f, purpose_key: e.target.value }))}
                                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                        placeholder="clinical_research"
                                    />
                                    <p className="text-xs text-gray-500 mt-1">Allowed: letters, numbers, _, -, :</p>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Label</label>
                                    <input
                                        type="text"
                                        value={purposeForm.label}
                                        onChange={e => setPurposeForm(f => ({ ...f, label: e.target.value }))}
                                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                        placeholder="Clinical Research"
                                    />
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                                    <textarea
                                        rows={3}
                                        value={purposeForm.description}
                                        onChange={e => setPurposeForm(f => ({ ...f, description: e.target.value }))}
                                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                        placeholder="Optional governance description"
                                    />
                                </div>
                                <button
                                    onClick={handleCreatePurpose}
                                    disabled={savingPurpose || !purposeForm.purpose_key.trim() || !purposeForm.label.trim()}
                                    className="w-full flex items-center justify-center space-x-2 py-2.5 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 text-sm font-medium disabled:opacity-50"
                                >
                                    <Plus className="h-4 w-4" />
                                    <span>{savingPurpose ? 'Saving...' : 'Create Purpose'}</span>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* ─── REVIEW MODAL ──────────────────────────────────── */}
            {reviewing && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                    <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6">
                        <h2 className="text-lg font-bold text-gray-900 mb-4">Review Access Request</h2>
                        <div className="bg-gray-50 rounded-lg p-4 mb-4">
                            <p className="text-sm font-medium">{reviewing.collection_name}</p>
                            <p className="text-xs text-gray-500">{reviewing.requesting_org_id} · {reviewing.trial_count} trials · {reviewing.total_patients} patients</p>
                            <p className="text-sm text-gray-700 mt-2">{reviewing.justification}</p>
                            {extractRequestedPurposes(reviewing.scope).length > 0 && (
                                <div className="mt-3">
                                    <p className="text-xs font-medium text-gray-600 mb-1">Requested Purpose(s)</p>
                                    <div className="flex flex-wrap gap-1.5">
                                        {extractRequestedPurposes(reviewing.scope).map(p => (
                                            <span key={p} className="text-xs bg-emerald-50 text-emerald-700 px-2 py-0.5 rounded">
                                                {p}
                                            </span>
                                        ))}
                                    </div>
                                </div>
                            )}
                        </div>
                        <div className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1">Grant Duration (days)</label>
                                <input type="number" value={grantDays} onChange={e => setGrantDays(Number(e.target.value))}
                                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1">Notes</label>
                                <textarea rows={2} value={reviewNotes} onChange={e => setReviewNotes(e.target.value)}
                                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm" />
                            </div>
                        </div>
                        <div className="flex justify-end space-x-3 mt-6">
                            <button onClick={() => setReviewing(null)} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg">Cancel</button>
                            <button onClick={() => handleReview('reject')} className="flex items-center space-x-1 px-4 py-2 text-sm text-red-700 bg-red-50 rounded-lg hover:bg-red-100">
                                <XCircle className="h-4 w-4" /><span>Reject</span>
                            </button>
                            <button onClick={() => handleReview('approve')} className="flex items-center space-x-1 px-4 py-2 text-sm text-white bg-green-600 rounded-lg hover:bg-green-700">
                                <CheckCircle className="h-4 w-4" /><span>Approve</span>
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}