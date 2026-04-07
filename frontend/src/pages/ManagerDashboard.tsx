import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { UserProfile } from '../keycloak';
import { managerApi, AccessGrant, Cohort, Assignment } from '../api/client';
import StatusBadge from '../components/StatusBadge';
import EmptyState from '../components/EmptyState';
import {
    ShoppingBag, Users, FlaskConical, Shield,
    Plus, UserPlus, AlertTriangle
} from 'lucide-react';

interface Props { user: UserProfile }

export default function ManagerDashboard({ user }: Props) {
    const [grants, setGrants] = useState<AccessGrant[]>([]);
    const [cohorts, setCohorts] = useState<Cohort[]>([]);
    const [assignments, setAssignments] = useState<Assignment[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [activeTab, setActiveTab] = useState<'grants' | 'cohorts' | 'assignments'>('grants');

    // Assignment modal
    const [showAssign, setShowAssign] = useState(false);
    const [assignForm, setAssignForm] = useState({
        researcher_username: '',
        trial_id: '',
        cohort_id: '',
        access_level: 'individual' as 'individual' | 'aggregate',
        duration_days: 180,
    });

    useEffect(() => { loadAll(); }, []);

    async function loadAll() {
        setLoading(true);
        try {
            const [g, c, a] = await Promise.all([
                managerApi.listMyOrgGrants(),
                managerApi.listCohorts(),
                managerApi.listAssignments(),
            ]);
            setGrants(g.grants);
            setCohorts(c.cohorts);
            setAssignments(a.assignments);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Failed to load');
        } finally {
            setLoading(false);
        }
    }

    async function handleAssign() {
        try {
            await managerApi.assignResearcher({
                researcher_username: assignForm.researcher_username,
                trial_id: assignForm.trial_id || undefined,
                cohort_id: assignForm.cohort_id || undefined,
                access_level: assignForm.access_level,
                duration_days: assignForm.duration_days,
            });
            setShowAssign(false);
            loadAll();
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Assignment failed');
        }
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center py-20">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
            </div>
        );
    }

    return (
        <div>
            {/* Header */}
            <div className="flex justify-between items-start mb-8">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Manager Dashboard</h1>
                    <p className="mt-1 text-sm text-gray-500">
                        {user.organizationName} · Manage access, cohorts, and researcher assignments
                    </p>
                </div>
                <div className="flex space-x-3">
                    <Link
                        to="/marketplace"
                        className="flex items-center space-x-2 px-4 py-2 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 text-sm font-medium"
                    >
                        <ShoppingBag className="h-4 w-4" />
                        <span>Browse Marketplace</span>
                    </Link>
                    <Link
                        to="/cohorts/new"
                        className="flex items-center space-x-2 px-4 py-2 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 text-sm font-medium"
                    >
                        <FlaskConical className="h-4 w-4" />
                        <span>Build Cohort</span>
                    </Link>
                    <button
                        onClick={() => setShowAssign(true)}
                        className="flex items-center space-x-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium"
                    >
                        <UserPlus className="h-4 w-4" />
                        <span>Assign Researcher</span>
                    </button>
                </div>
            </div>

            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg flex items-center space-x-2">
                    <AlertTriangle className="h-5 w-5 text-red-500" />
                    <span className="text-red-700">{error}</span>
                    <button onClick={() => setError('')} className="ml-auto text-red-500">&times;</button>
                </div>
            )}

            {/* Stats */}
            <div className="grid grid-cols-3 gap-6 mb-8">
                <div className="bg-white rounded-lg shadow p-6 border-l-4 border-blue-500">
                    <p className="text-sm text-gray-500">Org Access Grants</p>
                    <p className="text-3xl font-bold">{grants.length}</p>
                </div>
                <div className="bg-white rounded-lg shadow p-6 border-l-4 border-purple-500">
                    <p className="text-sm text-gray-500">Cohorts</p>
                    <p className="text-3xl font-bold">{cohorts.length}</p>
                </div>
                <div className="bg-white rounded-lg shadow p-6 border-l-4 border-green-500">
                    <p className="text-sm text-gray-500">Researcher Assignments</p>
                    <p className="text-3xl font-bold">{assignments.filter(a => a.is_active).length}</p>
                </div>
            </div>

            {/* Tabs */}
            <div className="border-b border-gray-200 mb-6">
                <nav className="flex space-x-8">
                    {(['grants', 'cohorts', 'assignments'] as const).map((tab) => (
                        <button
                            key={tab}
                            onClick={() => setActiveTab(tab)}
                            className={`py-4 px-1 border-b-2 text-sm font-medium ${activeTab === tab
                                ? 'border-blue-500 text-blue-600'
                                : 'border-transparent text-gray-500 hover:text-gray-700'
                                }`}
                        >
                            {tab === 'grants' && `Org Grants (${grants.length})`}
                            {tab === 'cohorts' && `Cohorts (${cohorts.length})`}
                            {tab === 'assignments' && `Assignments (${assignments.length})`}
                        </button>
                    ))}
                </nav>
            </div>

            {/* Org Grants (ceiling) */}
            {activeTab === 'grants' && (
                <div className="space-y-4">
                    {grants.length === 0 ? (
                        <EmptyState
                            icon={<ShoppingBag className="h-12 w-12" />}
                            title="No access grants yet"
                            description="Browse the marketplace to request access to clinical trial data"
                            action={
                                <Link to="/marketplace" className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm">
                                    Browse Marketplace
                                </Link>
                            }
                        />
                    ) : (
                        grants.map((g) => (
                            <div key={g.grant_id} className="bg-white rounded-lg shadow p-6 flex justify-between items-center">
                                <div>
                                    <h3 className="font-medium text-gray-900">{g.asset_title}</h3>
                                    <p className="text-sm text-gray-500">
                                        Granted: {new Date(g.granted_at).toLocaleDateString()}
                                        {' · Expires: '}
                                        {new Date(g.expires_at).toLocaleDateString()}
                                    </p>
                                </div>
                                <StatusBadge status={g.is_active ? 'active' : 'expired'} />
                            </div>
                        ))
                    )}
                </div>
            )}

            {/* Cohorts */}
            {activeTab === 'cohorts' && (
                <div className="space-y-4">
                    {cohorts.length === 0 ? (
                        <EmptyState
                            icon={<FlaskConical className="h-12 w-12" />}
                            title="No cohorts yet"
                            description="Create cohorts to define patient subsets for your researchers"
                            action={
                                <Link to="/cohorts/new" className="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm">
                                    Build Cohort
                                </Link>
                            }
                        />
                    ) : (
                        cohorts.map((c) => (
                            <div key={c.cohort_id} className="bg-white rounded-lg shadow p-6">
                                <div className="flex justify-between items-start">
                                    <div>
                                        <h3 className="font-medium text-gray-900">{c.name}</h3>
                                        <p className="text-sm text-gray-500 mt-1">{c.description}</p>
                                        <div className="flex items-center space-x-4 mt-2">
                                            <span className="text-sm text-gray-600">
                                                <Users className="h-4 w-4 inline mr-1" />
                                                {c.patient_count} patients
                                            </span>
                                            <span className="text-sm text-gray-600">
                                                {c.trial_ids.length} trials
                                            </span>
                                            <StatusBadge status={c.is_dynamic ? 'dynamic' : 'static'} />
                                        </div>
                                        {/* Filter Details */}
                                        <div className="mt-3 flex flex-wrap gap-2">
                                            {c.filter_criteria && Object.entries(c.filter_criteria).map(([key, value]) => {
                                                if (!value || (Array.isArray(value) && value.length === 0) || key === 'trial_ids') return null;
                                                const displayKey = key.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
                                                const displayVal = Array.isArray(value) ? value.join(', ') : value;
                                                return (
                                                    <span key={key} className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-indigo-50 text-indigo-700 border border-indigo-100">
                                                        {displayKey}: {displayVal}
                                                    </span>
                                                );
                                            })}
                                        </div>
                                    </div>
                                    <div className="text-right text-sm text-gray-500">
                                        {c.assignments.length} researchers assigned
                                    </div>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            )}

            {/* Assignments */}
            {activeTab === 'assignments' && (
                <div className="bg-white rounded-lg shadow overflow-hidden">
                    <table className="min-w-full divide-y divide-gray-200">
                        <thead className="bg-gray-50">
                            <tr>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Researcher</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Trial / Cohort</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Access Level</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Expires</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-200">
                            {assignments.map((a) => (
                                <tr key={a.assignment_id} className="hover:bg-gray-50">
                                    <td className="px-6 py-4 text-sm font-medium text-gray-900">{a.researcher_id}</td>
                                    <td className="px-6 py-4 text-sm text-gray-500 font-mono">
                                        {a.trial_id ? `Trial: ${a.trial_id.slice(0, 8)}...` : `Cohort: ${a.cohort_id?.slice(0, 8)}...`}
                                    </td>
                                    <td className="px-6 py-4">
                                        <StatusBadge status={a.access_level === 'individual' ? 'sensitive' : 'standard'} />
                                        <span className="ml-2 text-sm text-gray-600">{a.access_level}</span>
                                    </td>
                                    <td className="px-6 py-4 text-sm text-gray-500">
                                        {new Date(a.expires_at).toLocaleDateString()}
                                    </td>
                                    <td className="px-6 py-4">
                                        <StatusBadge status={a.is_active ? 'active' : 'expired'} />
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}            {/* ─── Assign Researcher Modal ────────────────────────── */}
            {showAssign && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
                    <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 max-h-[90vh] overflow-y-auto">
                        <div className="flex justify-between items-center mb-4">
                            <h2 className="text-lg font-bold text-gray-900">Assign Researcher</h2>
                            <button onClick={() => setShowAssign(false)} className="text-gray-400 hover:text-gray-600 text-xl leading-none">&times;</button>
                        </div>
                        <div className="space-y-4">

                            {/* Researcher Username */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1">
                                    Researcher Username <span className="text-red-500">*</span>
                                </label>
                                <input
                                    type="text"
                                    value={assignForm.researcher_username}
                                    onChange={(e) => setAssignForm({ ...assignForm, researcher_username: e.target.value })}
                                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                                    placeholder="e.g. researcher-jane"
                                />
                            </div>

                            {/* Assignment Target Type Toggle */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                    Assign To <span className="text-red-500">*</span>
                                </label>
                                <div className="flex rounded-lg border border-gray-300 overflow-hidden">
                                    <button
                                        onClick={() => setAssignForm({ ...assignForm, trial_id: '', cohort_id: '' })}
                                        className={`flex-1 py-2 text-sm font-medium transition-colors ${!assignForm.cohort_id && !assignForm.trial_id || assignForm.trial_id
                                            ? 'bg-blue-600 text-white'
                                            : 'bg-white text-gray-700 hover:bg-gray-50'
                                            }`}
                                    >
                                        🧪 Clinical Trial
                                    </button>
                                    <button
                                        onClick={() => setAssignForm({ ...assignForm, trial_id: '', cohort_id: cohorts[0]?.cohort_id || '' })}
                                        className={`flex-1 py-2 text-sm font-medium transition-colors border-l border-gray-300 ${assignForm.cohort_id && !assignForm.trial_id
                                            ? 'bg-blue-600 text-white'
                                            : 'bg-white text-gray-700 hover:bg-gray-50'
                                            }`}
                                    >
                                        👥 Patient Cohort
                                    </button>
                                </div>
                            </div>

                            {/* Trial Selector */}
                            {(!assignForm.cohort_id) && (
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">
                                        Clinical Trial (from org grants)
                                    </label>
                                    {grants.length === 0 ? (
                                        <p className="text-sm text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                                            No trials granted to your organization yet. Browse the marketplace to request access.
                                        </p>
                                    ) : (
                                        <select
                                            value={assignForm.trial_id}
                                            onChange={(e) => setAssignForm({ ...assignForm, trial_id: e.target.value, cohort_id: '' })}
                                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500"
                                        >
                                            <option value="">Select a trial...</option>
                                            {grants.filter(g => g.is_active).map((g) => (
                                                <option key={g.grant_id} value={g.trial_id}>
                                                    {g.asset_title}
                                                </option>
                                            ))}
                                        </select>
                                    )}
                                </div>
                            )}

                            {/* Cohort Selector */}
                            {assignForm.cohort_id && (
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">
                                        Patient Cohort
                                    </label>
                                    {cohorts.length === 0 ? (
                                        <p className="text-sm text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                                            No cohorts yet. Build one first from the dashboard.
                                        </p>
                                    ) : (
                                        <select
                                            value={assignForm.cohort_id}
                                            onChange={(e) => setAssignForm({ ...assignForm, cohort_id: e.target.value, trial_id: '' })}
                                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500"
                                        >
                                            <option value="">Select a cohort...</option>
                                            {cohorts.map((c) => (
                                                <option key={c.cohort_id} value={c.cohort_id}>
                                                    {c.name} — {c.patient_count} patients · {c.trial_ids.length} trials
                                                </option>
                                            ))}
                                        </select>
                                    )}
                                    {assignForm.cohort_id && cohorts.find(c => c.cohort_id === assignForm.cohort_id) && (
                                        <div className="mt-2 text-xs text-gray-500 bg-gray-50 rounded-lg p-2">
                                            {(() => {
                                                const c = cohorts.find(x => x.cohort_id === assignForm.cohort_id)!;
                                                const criteria = c.filter_criteria;
                                                const tags = Object.entries(criteria).filter(([k, v]) =>
                                                    k !== 'trial_ids' && v && (!Array.isArray(v) || v.length > 0)
                                                );
                                                return tags.length > 0 ? (
                                                    <span>Filters: {tags.map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join(' · ')}</span>
                                                ) : <span>No additional patient filters applied</span>;
                                            })()}
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* Access Level & Duration */}
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Access Level</label>
                                    <select
                                        value={assignForm.access_level}
                                        onChange={(e) => setAssignForm({
                                            ...assignForm,
                                            access_level: e.target.value as 'individual' | 'aggregate',
                                        })}
                                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500"
                                    >
                                        <option value="individual">Individual (patient-level)</option>
                                        <option value="aggregate">Aggregate only (counts/stats)</option>
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-1">Duration (days)</label>
                                    <input
                                        type="number"
                                        min={1}
                                        max={365}
                                        value={assignForm.duration_days}
                                        onChange={(e) => setAssignForm({ ...assignForm, duration_days: Number(e.target.value) })}
                                        className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500"
                                    />
                                </div>
                            </div>

                            {/* Access Level hint */}
                            <p className="text-xs text-gray-500 bg-blue-50 border border-blue-100 rounded-lg px-3 py-2">
                                <strong>Individual</strong> — researcher can access patient-level records (names, labs, vitals).<br />
                                <strong>Aggregate</strong> — researcher can only see counts and statistical summaries.
                            </p>
                        </div>

                        <div className="flex justify-end space-x-3 mt-6">
                            <button onClick={() => setShowAssign(false)} className="px-4 py-2 text-sm text-gray-700 hover:bg-gray-100 rounded-lg">
                                Cancel
                            </button>
                            <button
                                onClick={handleAssign}
                                disabled={!assignForm.researcher_username || (!assignForm.trial_id && !assignForm.cohort_id)}
                                className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                Assign Researcher
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
