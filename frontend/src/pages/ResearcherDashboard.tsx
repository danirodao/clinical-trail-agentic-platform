import { useEffect, useState } from 'react';
import { UserProfile } from '../keycloak';
import {
    researcherApi,
    AccessSummary,
    TrialAccess,
    TrialCohortFilter,
    CohortFilterCriteria,
} from '../api/client';
import {
    Lock,
    Unlock,
    Eye,
    BarChart3,
    ChevronDown,
    ChevronRight,
    Filter,
    FlaskConical,
    Users,
    MapPin,
    Calendar,
    Tag,
    Heart,
    Shield,
    Database,
    TrendingUp,
    PieChart,
    AlertTriangle,
    Activity,
    MessageSquare, // <-- ADDED for the new tab icon
} from 'lucide-react';

// IMPORT THE NEW QUERY INTERFACE
import { QueryInterface } from '../components/researcher/QueryInterface';

interface Props {
    user: UserProfile;
}

export default function ResearcherDashboard({ user }: Props) {
    const [access, setAccess] = useState<AccessSummary | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [expandedTrials, setExpandedTrials] = useState<Set<string>>(new Set());

    // NEW: State to manage which tab is currently active
    const [activeTab, setActiveTab] = useState<'access' | 'query'>('access');

    useEffect(() => {
        researcherApi
            .getMyAccess()
            .then(setAccess)
            .catch((e) => setError(e.message))
            .finally(() => setLoading(false));
    }, []);

    const toggleTrial = (trialId: string) => {
        setExpandedTrials((prev) => {
            const next = new Set(prev);
            next.has(trialId) ? next.delete(trialId) : next.add(trialId);
            return next;
        });
    };

    const expandAll = (trials: TrialAccess[]) => {
        setExpandedTrials((prev) => {
            const next = new Set(prev);
            trials.forEach((t) => next.add(t.trial_id));
            return next;
        });
    };

    const collapseAll = (trials: TrialAccess[]) => {
        setExpandedTrials((prev) => {
            const next = new Set(prev);
            trials.forEach((t) => next.delete(t.trial_id));
            return next;
        });
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center py-20">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-green-600" />
            </div>
        );
    }

    if (error) {
        return (
            <div className="p-4 bg-red-50 border border-red-200 rounded-lg text-red-700">
                {error}
            </div>
        );
    }

    const summary = access?.access_summary;
    const trialAccess = access?.trial_access ?? [];

    const individualTrials = trialAccess.filter((t) => t.access_level === 'individual');
    const aggregateOnlyTrials = trialAccess.filter((t) => t.access_level === 'aggregate');

    return (
        <div>
            {/* Header */}
            <div className="mb-8">
                <h1 className="text-2xl font-bold text-gray-900">Researcher Dashboard</h1>
                <p className="mt-1 text-sm text-gray-500">
                    {user.organizationName} · Your data access summary
                </p>
            </div>

            {!summary?.has_any_access ? (
                <div className="bg-white rounded-lg shadow p-12 text-center">
                    <Lock className="h-16 w-16 text-gray-300 mx-auto mb-4" />
                    <h2 className="text-lg font-medium text-gray-900">No Access Yet</h2>
                    <p className="mt-2 text-sm text-gray-500 max-w-md mx-auto">
                        Your manager hasn't assigned you to any clinical trials or cohorts yet.
                        Contact your organization's manager to request assignment.
                    </p>
                </div>
            ) : (
                <>
                    {/* ── Tabs Navigation ── */}
                    <div className="border-b border-gray-200 mb-6">
                        <nav className="-mb-px flex space-x-8">
                            <button
                                onClick={() => setActiveTab('access')}
                                className={`whitespace-nowrap pb-4 px-1 border-b-2 font-medium text-sm flex items-center ${activeTab === 'access'
                                        ? 'border-blue-500 text-blue-600'
                                        : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                                    }`}
                            >
                                <Database className="w-4 h-4 mr-2" />
                                My Access
                            </button>
                            <button
                                onClick={() => setActiveTab('query')}
                                className={`whitespace-nowrap pb-4 px-1 border-b-2 font-medium text-sm flex items-center ${activeTab === 'query'
                                        ? 'border-blue-500 text-blue-600'
                                        : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                                    }`}
                            >
                                <MessageSquare className="w-4 h-4 mr-2" />
                                Agentic Semantic Query
                            </button>
                        </nav>
                    </div>

                    {/* ── Tab Content Routing ── */}
                    {activeTab === 'query' ? (
                        <QueryInterface accessibleTrials={trialAccess} />
                    ) : (
                        <>
                            {/* Access mode banner */}
                            <AccessBanner
                                aggregateOnly={summary.aggregate_only}
                                individualCount={individualTrials.length}
                                aggregateOnlyCount={aggregateOnlyTrials.length}
                            />

                            {/* Stat cards */}
                            <div className="grid grid-cols-4 gap-4 mb-8">
                                <StatCard
                                    label="Total Trials"
                                    value={trialAccess.length}
                                    sub="accessible trials"
                                    icon={<Database className="h-8 w-8 text-gray-500" />}
                                    border="border-gray-400"
                                />
                                <StatCard
                                    label="Patient-Level"
                                    value={individualTrials.length}
                                    sub="individual access"
                                    icon={<Eye className="h-8 w-8 text-green-500" />}
                                    border="border-green-500"
                                />
                                <StatCard
                                    label="Statistics Only"
                                    value={aggregateOnlyTrials.length}
                                    sub="aggregate access"
                                    icon={<BarChart3 className="h-8 w-8 text-blue-500" />}
                                    border="border-blue-500"
                                />
                                <StatCard
                                    label="Cohort Filters"
                                    value={trialAccess.reduce((s, t) => s + t.cohort_filters.length, 0)}
                                    sub="applied across trials"
                                    icon={<Filter className="h-8 w-8 text-purple-500" />}
                                    border="border-purple-500"
                                />
                            </div>

                            {/* ── Individual Access Section ──────────────────── */}
                            <TrialSection
                                title="Trials with Individual Access"
                                subtitle="Patient-level data available, filtered by assigned cohorts"
                                icon={<Eye className="h-5 w-5 text-green-600" />}
                                border="border-green-500"
                                badge="bg-green-100 text-green-800"
                                trials={individualTrials}
                                expandedTrials={expandedTrials}
                                onToggle={toggleTrial}
                                onExpandAll={() => expandAll(individualTrials)}
                                onCollapseAll={() => collapseAll(individualTrials)}
                                renderRow={(trial, isExpanded, onToggle) => (
                                    <IndividualTrialRow
                                        key={trial.trial_id}
                                        trial={trial}
                                        isExpanded={isExpanded}
                                        onToggle={onToggle}
                                    />
                                )}
                                emptyIcon={<Eye className="h-12 w-12 text-gray-300" />}
                                emptyTitle="No Individual Access"
                                emptyMessage="You don't have patient-level access to any trials. Ask your manager for individual-level assignments."
                            />

                            {/* ── Aggregate-Only Access Section ─────────────── */}
                            <AggregateSection
                                trials={aggregateOnlyTrials}
                                expandedTrials={expandedTrials}
                                onToggle={toggleTrial}
                                onExpandAll={() => expandAll(aggregateOnlyTrials)}
                                onCollapseAll={() => collapseAll(aggregateOnlyTrials)}
                                hasIndividualTrials={individualTrials.length > 0}
                            />
                        </>
                    )}
                </>
            )}
        </div>
    );
}

/* ================================================================
   Sub-components
   ================================================================ */

/* ── Access Banner ─────────────────────────────────────────── */

function AccessBanner({
    aggregateOnly,
    individualCount,
    aggregateOnlyCount,
}: {
    aggregateOnly: boolean;
    individualCount: number;
    aggregateOnlyCount: number;
}) {
    if (aggregateOnly) {
        return (
            <div className="rounded-lg p-4 mb-8 flex items-center space-x-3 bg-yellow-50 border border-yellow-200">
                <BarChart3 className="h-6 w-6 text-yellow-600 flex-shrink-0" />
                <div>
                    <p className="font-medium text-yellow-800">Aggregate Access Only</p>
                    <p className="text-sm text-yellow-700">
                        You can view aggregate statistics (counts, averages, distributions)
                        but not individual patient records. Ask your manager for
                        individual-level assignments if needed.
                    </p>
                </div>
            </div>
        );
    }

    if (aggregateOnlyCount > 0) {
        return (
            <div className="rounded-lg p-4 mb-8 flex items-center space-x-3 bg-blue-50 border border-blue-200">
                <TrendingUp className="h-6 w-6 text-blue-600 flex-shrink-0" />
                <div>
                    <p className="font-medium text-blue-800">Mixed Access Levels</p>
                    <p className="text-sm text-blue-700">
                        You have patient-level access to{' '}
                        <strong>{individualCount}</strong> trial(s) and aggregate-only
                        access to <strong>{aggregateOnlyCount}</strong> trial(s).
                    </p>
                </div>
            </div>
        );
    }

    return (
        <div className="rounded-lg p-4 mb-8 flex items-center space-x-3 bg-green-50 border border-green-200">
            <Unlock className="h-6 w-6 text-green-600 flex-shrink-0" />
            <div>
                <p className="font-medium text-green-800">Full Individual Access</p>
                <p className="text-sm text-green-700">
                    You have patient-level access to all {individualCount} assigned
                    trial(s). You can also run aggregate queries on these trials.
                </p>
            </div>
        </div>
    );
}

/* ── Stat Card ─────────────────────────────────────────────── */

function StatCard({
    label, value, sub, icon, border,
}: {
    label: string; value: number; sub: string;
    icon: React.ReactNode; border: string;
}) {
    return (
        <div className={`bg-white rounded-lg shadow p-5 border-l-4 ${border}`}>
            <div className="flex items-center justify-between">
                <div>
                    <p className="text-sm text-gray-500">{label}</p>
                    <p className="text-3xl font-bold text-gray-900">{value}</p>
                    <p className="text-xs text-gray-400">{sub}</p>
                </div>
                {icon}
            </div>
        </div>
    );
}

/* ── Generic Trial Section ─────────────────────────────────── */

function TrialSection({
    title, subtitle, icon, border, badge, trials,
    expandedTrials, onToggle, onExpandAll, onCollapseAll,
    renderRow,
    emptyIcon, emptyTitle, emptyMessage,
}: {
    title: string; subtitle: string; icon: React.ReactNode;
    border: string; badge: string;
    trials: TrialAccess[];
    expandedTrials: Set<string>;
    onToggle: (id: string) => void;
    onExpandAll: () => void; onCollapseAll: () => void;
    renderRow: (trial: TrialAccess, isExpanded: boolean, onToggle: () => void) => React.ReactNode;
    emptyIcon: React.ReactNode; emptyTitle: string; emptyMessage: string;
}) {
    return (
        <div className={`bg-white rounded-lg shadow mb-6 border-l-4 ${border}`}>
            <div className="px-6 py-4 border-b border-gray-200">
                <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-3">
                        {icon}
                        <div>
                            <h2 className="font-semibold text-gray-900">{title}</h2>
                            <p className="text-xs text-gray-500">{subtitle}</p>
                        </div>
                    </div>
                    {trials.length > 0 && (
                        <div className="flex items-center space-x-3">
                            <span className={`text-xs font-medium px-2.5 py-1 rounded-full ${badge}`}>
                                {trials.length} trial{trials.length !== 1 ? 's' : ''}
                            </span>
                            <button onClick={onExpandAll} className="text-xs text-blue-600 hover:text-blue-800">
                                Expand All
                            </button>
                            <span className="text-gray-300">|</span>
                            <button onClick={onCollapseAll} className="text-xs text-blue-600 hover:text-blue-800">
                                Collapse
                            </button>
                        </div>
                    )}
                </div>
            </div>

            {trials.length === 0 ? (
                <div className="px-6 py-8 text-center">
                    {emptyIcon}
                    <p className="text-sm font-medium text-gray-700 mt-3">{emptyTitle}</p>
                    <p className="text-xs text-gray-500 mt-1 max-w-sm mx-auto">{emptyMessage}</p>
                </div>
            ) : (
                <div className="divide-y divide-gray-100">
                    {trials.map((trial) =>
                        renderRow(trial, expandedTrials.has(trial.trial_id), () => onToggle(trial.trial_id))
                    )}
                </div>
            )}
        </div>
    );
}

/* ── Individual Trial Row ──────────────────────────────────── */

function IndividualTrialRow({
    trial, isExpanded, onToggle,
}: {
    trial: TrialAccess; isExpanded: boolean; onToggle: () => void;
}) {
    const hasFilters = trial.cohort_filters.length > 0;

    return (
        <div>
            <button
                onClick={onToggle}
                className="w-full px-6 py-4 flex items-center justify-between hover:bg-gray-50 transition-colors text-left"
            >
                <div className="flex items-center space-x-3 min-w-0">
                    {hasFilters ? (
                        isExpanded
                            ? <ChevronDown className="h-4 w-4 text-gray-400 flex-shrink-0" />
                            : <ChevronRight className="h-4 w-4 text-gray-400 flex-shrink-0" />
                    ) : (
                        <div className="w-4" />
                    )}
                    <div className="min-w-0">
                        <div className="flex items-center space-x-2 flex-wrap">
                            <span className="text-sm font-semibold text-blue-700">{trial.nct_id}</span>
                            <TrialMetaBadges trial={trial} />
                            {trial.is_unrestricted ? (
                                <span className="text-xs px-2 py-0.5 rounded-full bg-green-100 text-green-700">Unrestricted</span>
                            ) : hasFilters ? (
                                <span className="text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-700">Filtered</span>
                            ) : null}
                        </div>
                        <p className="text-sm text-gray-600 truncate mt-0.5">{trial.title}</p>
                    </div>
                </div>
                {hasFilters && (
                    <span className="text-xs text-gray-500 flex-shrink-0 ml-4">
                        {trial.cohort_filters.length} cohort{trial.cohort_filters.length !== 1 ? 's' : ''}
                    </span>
                )}
            </button>

            {isExpanded && hasFilters && (
                <div className="px-6 pb-4 ml-7 space-y-3">
                    {trial.cohort_filters.map((c) => (
                        <CohortCard key={c.cohort_id} cohort={c} />
                    ))}
                </div>
            )}
        </div>
    );
}

/* ── Aggregate Section ─────────────────────────────────────── */

function AggregateSection({
    trials, expandedTrials, onToggle, onExpandAll, onCollapseAll, hasIndividualTrials,
}: {
    trials: TrialAccess[];
    expandedTrials: Set<string>;
    onToggle: (id: string) => void;
    onExpandAll: () => void; onCollapseAll: () => void;
    hasIndividualTrials: boolean;
}) {
    return (
        <div className="bg-white rounded-lg shadow mb-6 border-l-4 border-blue-500">
            <div className="px-6 py-4 border-b border-gray-200">
                <div className="flex items-center justify-between">
                    <div className="flex items-center space-x-3">
                        <BarChart3 className="h-5 w-5 text-blue-600" />
                        <div>
                            <h2 className="font-semibold text-gray-900">
                                Trials with Aggregate Access Only
                            </h2>
                            <p className="text-xs text-gray-500">
                                Summary statistics only — no individual patient records
                            </p>
                        </div>
                    </div>
                    {trials.length > 0 && (
                        <div className="flex items-center space-x-3">
                            <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-blue-100 text-blue-800">
                                {trials.length} trial{trials.length !== 1 ? 's' : ''}
                            </span>
                            <button onClick={onExpandAll} className="text-xs text-blue-600 hover:text-blue-800">
                                Expand All
                            </button>
                            <span className="text-gray-300">|</span>
                            <button onClick={onCollapseAll} className="text-xs text-blue-600 hover:text-blue-800">
                                Collapse
                            </button>
                        </div>
                    )}
                </div>
            </div>

            {trials.length === 0 ? (
                <div className="px-6 py-8 text-center">
                    {hasIndividualTrials ? (
                        <>
                            <PieChart className="h-10 w-10 text-green-300 mx-auto mb-3" />
                            <p className="text-sm font-medium text-gray-700">No aggregate-only trials</p>
                            <p className="text-xs text-gray-500 mt-1 max-w-sm mx-auto">
                                All your accessible trials have individual (patient-level) access,
                                which includes aggregate capabilities.
                            </p>
                        </>
                    ) : (
                        <>
                            <BarChart3 className="h-10 w-10 text-gray-300 mx-auto mb-3" />
                            <p className="text-sm font-medium text-gray-700">No aggregate access</p>
                            <p className="text-xs text-gray-500 mt-1 max-w-sm mx-auto">
                                Contact your manager to request aggregate access to clinical trials.
                            </p>
                        </>
                    )}
                </div>
            ) : (
                <div className="divide-y divide-gray-100">
                    {trials.map((trial) => (
                        <AggregateTrialRow
                            key={trial.trial_id}
                            trial={trial}
                            isExpanded={expandedTrials.has(trial.trial_id)}
                            onToggle={() => onToggle(trial.trial_id)}
                        />
                    ))}
                </div>
            )}

            {trials.length > 0 && (
                <div className="px-6 py-3 bg-blue-50 border-t border-blue-100">
                    <div className="flex items-start space-x-2">
                        <AlertTriangle className="h-4 w-4 text-blue-500 mt-0.5 flex-shrink-0" />
                        <p className="text-xs text-blue-700">
                            <strong>Aggregate access</strong> allows you to query counts,
                            averages, and distributions but not view individual patient
                            records. Contact your manager to upgrade to individual access.
                        </p>
                    </div>
                </div>
            )}
        </div>
    );
}

/* ── Aggregate Trial Row ───────────────────────────────────── */

function AggregateTrialRow({
    trial, isExpanded, onToggle,
}: {
    trial: TrialAccess; isExpanded: boolean; onToggle: () => void;
}) {
    const hasFilters = trial.cohort_filters.length > 0;

    return (
        <div>
            <button
                onClick={onToggle}
                className="w-full px-6 py-4 flex items-center justify-between hover:bg-blue-50/50 transition-colors text-left"
            >
                <div className="flex items-center space-x-3 min-w-0">
                    {isExpanded
                        ? <ChevronDown className="h-4 w-4 text-gray-400 flex-shrink-0" />
                        : <ChevronRight className="h-4 w-4 text-gray-400 flex-shrink-0" />
                    }
                    <BarChart3 className="h-4 w-4 text-blue-500 flex-shrink-0" />
                    <div className="min-w-0">
                        <div className="flex items-center space-x-2 flex-wrap">
                            <span className="text-sm font-semibold text-blue-700">{trial.nct_id}</span>
                            <TrialMetaBadges trial={trial} />
                            <span className="text-xs px-2 py-0.5 rounded-full bg-blue-100 text-blue-700">
                                Statistics Only
                            </span>
                            {hasFilters && (
                                <span className="text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-700">
                                    Filtered
                                </span>
                            )}
                        </div>
                        <p className="text-sm text-gray-600 truncate mt-0.5">{trial.title}</p>
                    </div>
                </div>

                <div className="flex items-center space-x-3 flex-shrink-0 ml-4">
                    {trial.patient_count > 0 && (
                        <span className="text-xs text-gray-500">
                            {trial.patient_count} patients
                        </span>
                    )}
                </div>
            </button>

            {isExpanded && (
                <div className="px-6 pb-4 ml-7 space-y-3">
                    {/* Trial details */}
                    <div className="bg-gray-50 rounded-lg p-4 border border-gray-200">
                        <p className="text-xs font-medium text-gray-700 mb-2">Trial Details</p>
                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                            {trial.phase && (
                                <DetailItem icon={<FlaskConical className="h-3 w-3 text-indigo-500" />} label="Phase" value={trial.phase} />
                            )}
                            {trial.therapeutic_area && (
                                <DetailItem icon={<Tag className="h-3 w-3 text-teal-500" />} label="Therapeutic Area" value={trial.therapeutic_area} />
                            )}
                            {trial.overall_status && (
                                <DetailItem icon={<Activity className="h-3 w-3 text-green-500" />} label="Status" value={trial.overall_status} />
                            )}
                            {trial.patient_count > 0 && (
                                <DetailItem icon={<Users className="h-3 w-3 text-blue-500" />} label="Patients" value={trial.patient_count.toString()} />
                            )}
                        </div>
                    </div>

                    {/* Allowed queries */}
                    <div className="bg-blue-50 rounded-lg p-3 border border-blue-200">
                        <p className="text-xs font-medium text-blue-800 mb-2">
                            Available aggregate queries:
                        </p>
                        <div className="flex flex-wrap gap-2">
                            {[
                                'Patient counts',
                                'Age distribution',
                                'Sex breakdown',
                                'Adverse event rates',
                                'Enrollment statistics',
                                'Outcome summaries',
                            ].map((q) => (
                                <span key={q} className="text-xs px-2 py-1 rounded bg-white border border-blue-200 text-blue-700">
                                    {q}
                                </span>
                            ))}
                        </div>
                    </div>

                    {/* Cohort filters if any */}
                    {hasFilters && trial.cohort_filters.map((c) => (
                        <CohortCard key={c.cohort_id} cohort={c} />
                    ))}

                    {/* Restriction notice */}
                    <div className="flex items-start space-x-2 p-3 bg-yellow-50 rounded-lg border border-yellow-200">
                        <Lock className="h-4 w-4 text-yellow-600 mt-0.5 flex-shrink-0" />
                        <p className="text-xs text-yellow-800">
                            <strong>Individual records restricted.</strong> You cannot view
                            patient-level data for this trial. Contact your manager to
                            upgrade to individual access.
                        </p>
                    </div>
                </div>
            )}
        </div>
    );
}

/* ── Trial Meta Badges ─────────────────────────────────────── */

function TrialMetaBadges({ trial }: { trial: TrialAccess }) {
    return (
        <>
            {trial.phase && (
                <span className="text-xs px-1.5 py-0.5 rounded bg-indigo-50 text-indigo-700 border border-indigo-200">
                    {trial.phase}
                </span>
            )}
            {trial.therapeutic_area && (
                <span className="text-xs px-1.5 py-0.5 rounded bg-teal-50 text-teal-700 border border-teal-200">
                    {trial.therapeutic_area}
                </span>
            )}
        </>
    );
}

/* ── Detail Item ───────────────────────────────────────────── */

function DetailItem({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
    return (
        <div className="flex items-start space-x-2">
            <div className="mt-0.5">{icon}</div>
            <div>
                <p className="text-xs text-gray-500">{label}</p>
                <p className="text-xs font-medium text-gray-800">{value}</p>
            </div>
        </div>
    );
}

/* ── Cohort Card ───────────────────────────────────────────── */

function CohortCard({ cohort }: { cohort: TrialCohortFilter }) {
    const tags = buildFilterTags(cohort.filter_criteria);

    return (
        <div className="bg-gray-50 rounded-lg p-4 border border-gray-200">
            <div className="flex items-center space-x-2 mb-3">
                <Filter className="h-4 w-4 text-purple-600" />
                <span className="text-sm font-medium text-gray-900">{cohort.cohort_name}</span>
            </div>

            {tags.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                    {tags.map((t, i) => (
                        <span
                            key={i}
                            className="inline-flex items-center space-x-1 text-xs px-2.5 py-1 rounded-full bg-white border border-gray-200 text-gray-700"
                        >
                            {t.icon}
                            <span>{t.label}</span>
                        </span>
                    ))}
                </div>
            ) : (
                <p className="text-xs text-gray-500 italic">No additional filters — full trial population</p>
            )}
        </div>
    );
}

/* ── Filter Tag Builder ────────────────────────────────────── */

interface FilterTag { icon: React.ReactNode; label: string }

function buildFilterTags(c: CohortFilterCriteria): FilterTag[] {
    const tags: FilterTag[] = [];

    const hasMin = c.age_min !== undefined && c.age_min > 0;
    const hasMax = c.age_max !== undefined && c.age_max < 100;
    if (hasMin || hasMax) {
        tags.push({ icon: <Calendar className="h-3 w-3 text-blue-500" />, label: `Age ${c.age_min ?? 0}–${c.age_max ?? 100}` });
    }
    if (c.sex?.length) {
        tags.push({ icon: <Users className="h-3 w-3 text-pink-500" />, label: `Sex: ${c.sex.join(', ')}` });
    }
    c.ethnicity?.forEach((e) =>
        tags.push({ icon: <Users className="h-3 w-3 text-orange-500" />, label: e })
    );
    c.conditions?.forEach((cond) =>
        tags.push({ icon: <Heart className="h-3 w-3 text-red-500" />, label: cond })
    );
    if (c.phases?.length) {
        tags.push({ icon: <FlaskConical className="h-3 w-3 text-indigo-500" />, label: c.phases.join(', ') });
    }
    c.country?.forEach((co) =>
        tags.push({ icon: <MapPin className="h-3 w-3 text-green-500" />, label: co })
    );
    c.therapeutic_areas?.forEach((a) =>
        tags.push({ icon: <Tag className="h-3 w-3 text-teal-500" />, label: a })
    );
    if (c.disposition_status?.length) {
        tags.push({ icon: <Shield className="h-3 w-3 text-gray-500" />, label: `Status: ${c.disposition_status.join(', ')}` });
    }
    c.arm_assigned?.forEach((arm) =>
        tags.push({ icon: <Database className="h-3 w-3 text-cyan-500" />, label: arm })
    );

    return tags;
}