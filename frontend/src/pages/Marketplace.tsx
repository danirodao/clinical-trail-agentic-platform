import { useEffect, useState } from 'react';
import { UserProfile } from '../keycloak';
import { managerApi, MarketplaceCollection, CollectionDetail } from '../api/client';
import StatusBadge from '../components/StatusBadge';
import {
    Search, Package, Lock, CheckCircle, Clock, ChevronRight,
    FlaskConical, Heart, Pill, Globe, Users, Zap, ArrowLeft
} from 'lucide-react';

interface Props { user: UserProfile }

const areaIcons: Record<string, typeof FlaskConical> = {
    Oncology: FlaskConical, Cardiology: Heart, Endocrinology: Pill,
};

const accessColors: Record<string, string> = {
    full_access: 'border-green-500 bg-green-50',
    partial_access: 'border-yellow-500 bg-yellow-50',
    pending: 'border-blue-500 bg-blue-50',
    no_access: 'border-gray-200 bg-white',
};

export default function Marketplace({ user }: Props) {
    const [collections, setCollections] = useState<MarketplaceCollection[]>([]);
    const [filtered, setFiltered] = useState<MarketplaceCollection[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [error, setError] = useState('');

    // Detail view
    const [detail, setDetail] = useState<CollectionDetail | null>(null);
    const [loadingDetail, setLoadingDetail] = useState(false);

    // Request modal
    const [requesting, setRequesting] = useState<MarketplaceCollection | null>(null);
    const [justification, setJustification] = useState('');
    const [duration, setDuration] = useState(365);
    const [submitting, setSubmitting] = useState(false);

    useEffect(() => { loadMarketplace(); }, []);
    useEffect(() => {
        const q = search.toLowerCase();
        setFiltered(q ? collections.filter(c =>
            c.name.toLowerCase().includes(q) ||
            c.description?.toLowerCase().includes(q) ||
            c.therapeutic_areas?.some(a => a.toLowerCase().includes(q)) ||
            c.drug_names?.some(d => d.toLowerCase().includes(q)) ||
            c.condition_names?.some(cn => cn.toLowerCase().includes(q))
        ) : collections);
    }, [collections, search]);

    async function loadMarketplace() {
        try {
            const resp = await managerApi.browseMarketplace();
            setCollections(resp.collections);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Load failed');
        } finally {
            setLoading(false);
        }
    }

    async function openDetail(id: string) {
        setLoadingDetail(true);
        try {
            const d = await managerApi.getCollectionDetail(id);
            setDetail(d);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Failed to load detail');
        } finally {
            setLoadingDetail(false);
        }
    }

    async function handleRequest() {
        if (!requesting || !justification.trim()) return;
        setSubmitting(true);
        try {
            await managerApi.requestAccess({
                collection_id: requesting.collection_id,
                justification,
                requested_duration_days: duration,
            });
            setRequesting(null);
            setJustification('');
            loadMarketplace();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Request failed');
        } finally {
            setSubmitting(false);
        }
    }

    if (loading) {
        return <div className="flex justify-center py-20"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" /></div>;
    }

    // Detail view
    if (detail) {
        return (
            <div>
                <button onClick={() => setDetail(null)} className="flex items-center text-sm text-gray-500 hover:text-gray-700 mb-4">
                    <ArrowLeft className="h-4 w-4 mr-1" /> Back to Marketplace
                </button>
                <div className="bg-white rounded-lg shadow p-6 mb-6">
                    <h1 className="text-xl font-bold text-gray-900">{detail.collection.name}</h1>
                    <p className="text-sm text-gray-500 mt-1">{detail.collection.description}</p>
                    <div className="flex items-center gap-3 mt-3">
                        <span className="text-sm"><strong>{detail.collection.trial_count}</strong> trials</span>
                        <span className="text-sm"><strong>{detail.collection.total_patients}</strong> patients</span>
                        <StatusBadge status={detail.collection.sensitivity_level} />
                    </div>
                    <div className="mt-4 p-3 bg-gray-50 rounded-lg">
                        <p className="text-sm font-medium">Your Access: {detail.access.granted_count}/{detail.access.total_count} trials granted</p>
                    </div>
                </div>
                <div className="bg-white rounded-lg shadow overflow-hidden">
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Trial</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Phase</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Area</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Patients</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Drugs</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-100">
                            {detail.trials.map(t => (
                                <tr key={t.trial_id} className="hover:bg-gray-50">
                                    <td className="px-6 py-3">
                                        <p className="text-sm font-medium text-gray-900">{t.title || t.nct_id}</p>
                                        <p className="text-xs text-gray-400">{t.nct_id}</p>
                                    </td>
                                    <td className="px-6 py-3 text-sm text-gray-500">{t.phase}</td>
                                    <td className="px-6 py-3 text-sm text-gray-500">{t.therapeutic_area}</td>
                                    <td className="px-6 py-3 text-sm text-gray-500">{t.patient_count}</td>
                                    <td className="px-6 py-3 text-xs text-gray-500">{t.drug_names?.join(', ')}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        );
    }

    return (
        <div>
            <div className="mb-6">
                <h1 className="text-2xl font-bold text-gray-900">Data Marketplace</h1>
                <p className="text-sm text-gray-500 mt-1">Browse trial collections and request access for {user.organizationName}</p>
            </div>

            {error && (
                <div className="mb-4 p-3 bg-red-50 rounded-lg text-red-700 text-sm">{error}
                    <button onClick={() => setError('')} className="ml-4">&times;</button>
                </div>
            )}

            <div className="relative mb-6">
                <Search className="absolute left-3 top-2.5 h-5 w-5 text-gray-400" />
                <input type="text" value={search} onChange={e => setSearch(e.target.value)}
                    placeholder="Search by name, therapeutic area, drug, condition..."
                    className="w-full pl-10 pr-4 py-2.5 border border-gray-300 rounded-lg text-sm" />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {filtered.map(c => {
                    const Icon = c.therapeutic_areas?.[0] ? (areaIcons[c.therapeutic_areas[0]] || Package) : Package;
                    return (
                        <div key={c.collection_id} className={`rounded-lg shadow border-l-4 overflow-hidden ${accessColors[c.access_status]}`}>
                            <div className="p-6">
                                <div className="flex items-start justify-between mb-3">
                                    <div className="p-2 bg-white rounded-lg shadow-sm"><Icon className="h-6 w-6 text-indigo-600" /></div>
                                    <div className="flex items-center gap-2">
                                        {c.is_dynamic && <Zap className="h-4 w-4 text-purple-500" title="Dynamic collection" />}
                                        <StatusBadge status={c.sensitivity_level} />
                                    </div>
                                </div>
                                <h3 className="text-base font-semibold text-gray-900">{c.name}</h3>
                                <p className="text-xs text-gray-500 mt-1 line-clamp-2">{c.description}</p>
                                <div className="flex flex-wrap gap-2 mt-3">
                                    {c.therapeutic_areas?.map(a => <span key={a} className="text-xs bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded">{a}</span>)}
                                    {c.phases?.map(p => <span key={p} className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded">{p}</span>)}
                                </div>
                                <div className="flex items-center gap-4 mt-3 text-xs text-gray-500">
                                    <span><Package className="h-3.5 w-3.5 inline mr-1" />{c.trial_count} trials</span>
                                    <span><Users className="h-3.5 w-3.5 inline mr-1" />{c.total_patients} patients</span>
                                    {c.regions?.length > 0 && <span><Globe className="h-3.5 w-3.5 inline mr-1" />{c.regions.join(', ')}</span>}
                                </div>
                                {c.drug_names?.length > 0 && (
                                    <p className="text-xs text-gray-400 mt-2">Drugs: {c.drug_names.slice(0, 5).join(', ')}{c.drug_names.length > 5 ? '...' : ''}</p>
                                )}
                            </div>
                            <div className="px-6 py-3 bg-white/80 border-t border-gray-100 flex justify-between items-center">
                                <button onClick={() => openDetail(c.collection_id)} className="text-xs text-indigo-600 hover:text-indigo-700 flex items-center">
                                    View Details <ChevronRight className="h-3 w-3 ml-1" />
                                </button>
                                {c.access_status === 'full_access' ? (
                                    <span className="flex items-center text-xs text-green-700 font-medium"><CheckCircle className="h-4 w-4 mr-1" />Full Access</span>
                                ) : c.access_status === 'pending' ? (
                                    <span className="flex items-center text-xs text-blue-700 font-medium"><Clock className="h-4 w-4 mr-1" />Pending</span>
                                ) : (
                                    <button onClick={() => setRequesting(c)}
                                        className="flex items-center space-x-1 px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-xs font-medium">
                                        <Lock className="h-3.5 w-3.5" /><span>Request Access</span>
                                    </button>
                                )}
                            </div>
                        </div>
                    );
                })}
            </div>

            {/* Request Modal */}
            {requesting && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                    <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6">
                        <h2 className="text-lg font-bold text-gray-900 mb-2">Request Access</h2>
                        <div className="bg-gray-50 rounded-lg p-4 mb-4">
                            <p className="text-sm font-medium">{requesting.name}</p>
                            <p className="text-xs text-gray-500">{requesting.trial_count} trials · {requesting.total_patients} patients</p>
                            <p className="text-xs text-gray-500">For: {user.organizationName}</p>
                        </div>
                        <div className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1">Justification <span className="text-red-500">*</span></label>
                                <textarea rows={4} value={justification} onChange={e => setJustification(e.target.value)}
                                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                    placeholder="Explain your research purpose and why your organization needs access..." />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1">Duration</label>
                                <select value={duration} onChange={e => setDuration(Number(e.target.value))}
                                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm">
                                    <option value={90}>90 days</option>
                                    <option value={180}>6 months</option>
                                    <option value={365}>1 year</option>
                                    <option value={730}>2 years</option>
                                </select>
                            </div>
                        </div>
                        <div className="flex justify-end space-x-3 mt-6">
                            <button onClick={() => { setRequesting(null); setJustification(''); }}
                                className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg">Cancel</button>
                            <button onClick={handleRequest} disabled={submitting || justification.length < 20}
                                className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50">
                                {submitting ? 'Submitting...' : 'Submit Request'}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}