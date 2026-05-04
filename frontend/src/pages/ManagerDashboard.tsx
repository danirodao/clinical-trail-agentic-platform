import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { UserProfile } from '../keycloak';
import { managerApi, AccessGrant, Cohort, Assignment } from '../api/client';
import StatusBadge from '../components/StatusBadge';
import EmptyState from '../components/EmptyState';
import {
    ShoppingBag, Users, FlaskConical,
    UserPlus, AlertTriangle, Lock
} from 'lucide-react';

interface Props { user: UserProfile }

type AssignmentScopeKey = 'region' | 'area' | 'phase' | 'purpose';
type AssignmentScopeState = Record<AssignmentScopeKey, string[]>;
type AssignmentScopeOptionConfig = Record<AssignmentScopeKey, {
    options: string[];
    constrained: boolean;
    constrainedTrials: number;
    totalTrials: number;
}>;
type TrialScopeEntry = {
    raw: Record<string, unknown>;
    effective: Record<string, unknown>;
};

const RESTRICTION_LABELS: Record<string, string> = {
    permitted_regions: 'Regions',
    regions: 'Regions',
    region: 'Regions',
    permitted_areas: 'Therapeutic Areas',
    therapeutic_areas: 'Therapeutic Areas',
    areas: 'Therapeutic Areas',
    area: 'Therapeutic Areas',
    permitted_phases: 'Phases',
    phases: 'Phases',
    phase: 'Phase',
    approved_purposes: 'Purposes',
    purposes: 'Purposes',
    purpose: 'Purpose',
    minimum_cohort_size: 'Min Cohort Size',
    resource_classification: 'Classification',
    age_min: 'Min Age',
    age_max: 'Max Age',
    sex: 'Sex',
    ethnicity: 'Ethnicity',
    country: 'Country',
};

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
    const [assignmentScope, setAssignmentScope] = useState<AssignmentScopeState>({
        region: [],
        area: [],
        phase: [],
        purpose: [],
    });
    const [visiblePatientsBaseline, setVisiblePatientsBaseline] = useState<number | null>(null);
    const [visiblePatientsCurrent, setVisiblePatientsCurrent] = useState<number | null>(null);
    const [visiblePatientsTrialCount, setVisiblePatientsTrialCount] = useState<number>(0);
    const [visiblePatientsLoading, setVisiblePatientsLoading] = useState(false);
    const [visiblePatientsError, setVisiblePatientsError] = useState('');

    useEffect(() => { loadAll(); }, []);

    const shortId = (id?: string) => (id ? `${id.slice(0, 8)}...` : 'unknown');

    const resolveTrialLabel = (trialId?: string) => {
        if (!trialId) return null;
        const grant = grants.find((g) => g.trial_id === trialId);
        if (grant?.asset_title) {
            return `${grant.asset_title} (${shortId(trialId)})`;
        }
        return `Trial ${shortId(trialId)}`;
    };

    const resolveCohortLabel = (cohortId?: string) => {
        if (!cohortId) return null;
        const cohort = cohorts.find((c) => c.cohort_id === cohortId);
        if (cohort?.name) {
            return `${cohort.name} (${shortId(cohortId)})`;
        }
        return `Cohort ${shortId(cohortId)}`;
    };

    const toTitle = (rawKey: string) =>
        RESTRICTION_LABELS[rawKey]
        ?? rawKey
            .split('_')
            .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
            .join(' ');

    const buildRestrictionTags = (source?: Record<string, unknown>) => {
        if (!source) return [] as string[];
        return Object.entries(source).flatMap(([key, value]) => {
            if (value === null || value === undefined) return [];
            if (Array.isArray(value)) {
                const cleaned = value.map((v) => String(v).trim()).filter(Boolean);
                if (cleaned.length === 0) return [];
                return [`${toTitle(key)}: ${cleaned.join(', ')}`];
            }
            if (typeof value === 'boolean') {
                return [`${toTitle(key)}: ${value ? 'Yes' : 'No'}`];
            }
            if (typeof value === 'string' || typeof value === 'number') {
                const text = String(value).trim();
                if (!text) return [];
                return [`${toTitle(key)}: ${text}`];
            }
            return [];
        });
    };

    const assignmentRestrictionTags = (assignment: Assignment) => {
        const merged = new Set<string>([
            ...buildRestrictionTags(assignment.assignment_scope),
            ...buildRestrictionTags(assignment.trial_grant_scope),
            ...buildRestrictionTags(assignment.cohort_grant_scope),
            ...buildRestrictionTags(assignment.cohort_filter_criteria as Record<string, unknown> | undefined),
        ]);
        return Array.from(merged);
    };

    const getScopeValues = (scope: Record<string, unknown> | undefined, keys: string[]) => {
        if (!scope) return [] as string[];
        const values = new Set<string>();
        keys.forEach((key) => {
            const value = scope[key];
            if (Array.isArray(value)) {
                value
                    .map((item) => String(item).trim())
                    .filter(Boolean)
                    .forEach((item) => values.add(item));
                return;
            }
            if (typeof value === 'string' || typeof value === 'number') {
                const text = String(value).trim();
                if (text) values.add(text);
            }
        });
        return Array.from(values).sort((a, b) => a.localeCompare(b));
    };

    const hasAnyScopeKey = (scope: Record<string, unknown> | undefined, keys: string[]) => {
        if (!scope) return false;
        return keys.some((key) => Object.prototype.hasOwnProperty.call(scope, key));
    };

    const intersectOptions = (all: string[][]) => {
        if (all.length === 0) return [] as string[];
        const sets = all.map((vals) => new Set(vals));
        const first = all[0] ?? [];
        return first.filter((item) => sets.every((s) => s.has(item))).sort((a, b) => a.localeCompare(b));
    };

    const buildDimensionOptionConfig = (
        trialScopeEntries: TrialScopeEntry[],
        keys: string[],
    ) => {
        const constrainedEntries = trialScopeEntries.filter((entry) => hasAnyScopeKey(entry.raw, keys));

        if (constrainedEntries.length > 0) {
            return {
                options: intersectOptions(
                    constrainedEntries
                        .map((entry) => getScopeValues(entry.effective, keys))
                        .filter((values) => values.length > 0),
                ),
                constrained: true,
                constrainedTrials: constrainedEntries.length,
                totalTrials: trialScopeEntries.length,
            };
        }

        const unconstrainedOptions = intersectOptions(
            trialScopeEntries
                .map((entry) => getScopeValues(entry.effective, keys))
                .filter((values) => values.length > 0),
        );

        if (trialScopeEntries.length === 0) {
            return {
                options: [] as string[],
                constrained: false,
                constrainedTrials: 0,
                totalTrials: 0,
            };
        }

        return {
            options: unconstrainedOptions,
            constrained: false,
            constrainedTrials: 0,
            totalTrials: trialScopeEntries.length,
        };
    };

    const buildEffectiveScopeForGrant = (grant: AccessGrant): Record<string, unknown> => {
        const scope: Record<string, unknown> = { ...(grant.scope || {}) };

        if (!hasAnyScopeKey(scope, ['permitted_regions', 'regions', 'region']) && (grant.trial_regions?.length ?? 0) > 0) {
            scope.regions = (grant.trial_regions || []).filter((v) => String(v).trim());
        }

        if (!hasAnyScopeKey(scope, ['permitted_phases', 'phases', 'phase']) && (grant.trial_phases?.length ?? 0) > 0) {
            scope.phases = (grant.trial_phases || []).filter((v) => String(v).trim());
        }

        if (!hasAnyScopeKey(scope, ['permitted_areas', 'therapeutic_areas', 'areas', 'area']) && grant.trial_therapeutic_area) {
            scope.therapeutic_areas = [grant.trial_therapeutic_area];
        }

        return scope;
    };

    const assignmentScopeOptions = useMemo<AssignmentScopeOptionConfig>(() => {
        const trialScopeEntries: TrialScopeEntry[] = [];
        if (assignForm.trial_id) {
            const grant = grants.find((g) => g.trial_id === assignForm.trial_id && g.is_active);
            if (grant) {
                trialScopeEntries.push({
                    raw: { ...(grant.scope || {}) },
                    effective: buildEffectiveScopeForGrant(grant),
                });
            }
        }
        if (assignForm.cohort_id) {
            const cohort = cohorts.find((c) => c.cohort_id === assignForm.cohort_id);
            if (cohort) {
                cohort.trial_ids.forEach((tid) => {
                    const grant = grants.find((g) => g.trial_id === tid && g.is_active);
                    if (grant) {
                        trialScopeEntries.push({
                            raw: { ...(grant.scope || {}) },
                            effective: buildEffectiveScopeForGrant(grant),
                        });
                    }
                });
            }
        }

        return {
            region: buildDimensionOptionConfig(trialScopeEntries, ['permitted_regions', 'regions', 'region']),
            area: buildDimensionOptionConfig(trialScopeEntries, ['permitted_areas', 'therapeutic_areas', 'areas', 'area']),
            phase: buildDimensionOptionConfig(trialScopeEntries, ['permitted_phases', 'phases', 'phase']),
            purpose: buildDimensionOptionConfig(trialScopeEntries, ['approved_purposes', 'purposes', 'purpose']),
        };
    }, [assignForm.cohort_id, assignForm.trial_id, cohorts, grants]);

    useEffect(() => {
        setAssignmentScope((prev) => ({
            region: assignmentScopeOptions.region.constrained
                ? [...assignmentScopeOptions.region.options]
                : prev.region.filter((v) => assignmentScopeOptions.region.options.includes(v)),
            area: assignmentScopeOptions.area.constrained
                ? [...assignmentScopeOptions.area.options]
                : prev.area.filter((v) => assignmentScopeOptions.area.options.includes(v)),
            phase: assignmentScopeOptions.phase.constrained
                ? [...assignmentScopeOptions.phase.options]
                : prev.phase.filter((v) => assignmentScopeOptions.phase.options.includes(v)),
            purpose: assignmentScopeOptions.purpose.constrained
                ? [...assignmentScopeOptions.purpose.options]
                : prev.purpose.filter((v) => assignmentScopeOptions.purpose.options.includes(v)),
        }));
    }, [assignmentScopeOptions]);

    const toggleScopeValue = (scopeKey: AssignmentScopeKey, value: string) => {
        if (assignmentScopeOptions[scopeKey].constrained) {
            return;
        }
        setAssignmentScope((prev) => {
            const values = prev[scopeKey];
            const exists = values.includes(value);
            return {
                ...prev,
                [scopeKey]: exists
                    ? values.filter((v) => v !== value)
                    : [...values, value],
            };
        });
    };

    const buildAssignmentScopePayload = () => {
        const assignmentScopePayload: Partial<AssignmentScopeState> = {};
        (['region', 'area', 'phase', 'purpose'] as AssignmentScopeKey[]).forEach((key) => {
            if (!assignmentScopeOptions[key].constrained && assignmentScope[key].length > 0) {
                assignmentScopePayload[key] = assignmentScope[key];
            }
        });
        const hasAssignmentScope = Object.values(assignmentScopePayload).some((v) => (v?.length ?? 0) > 0);
        if (!hasAssignmentScope || assignForm.access_level !== 'individual') {
            return undefined;
        }
        return {
            region: assignmentScopePayload.region,
            area: assignmentScopePayload.area,
            phase: assignmentScopePayload.phase,
            purpose: assignmentScopePayload.purpose,
        };
    };

    useEffect(() => {
        let cancelled = false;

        async function loadVisiblePatientsPreview() {
            if (!showAssign || (!assignForm.trial_id && !assignForm.cohort_id)) {
                setVisiblePatientsBaseline(null);
                setVisiblePatientsCurrent(null);
                setVisiblePatientsTrialCount(0);
                setVisiblePatientsError('');
                return;
            }

            setVisiblePatientsLoading(true);
            setVisiblePatientsError('');

            try {
                const basePayload = {
                    trial_id: assignForm.trial_id || undefined,
                    cohort_id: assignForm.cohort_id || undefined,
                    duration_days: assignForm.duration_days,
                };
                const narrowedScopePayload = buildAssignmentScopePayload();

                const [base, narrowed] = await Promise.all([
                    managerApi.previewAssignmentVisiblePatients(basePayload),
                    narrowedScopePayload
                        ? managerApi.previewAssignmentVisiblePatients({ ...basePayload, assignment_scope: narrowedScopePayload })
                        : Promise.resolve(null),
                ]);

                if (cancelled) return;
                setVisiblePatientsBaseline(base.visible_patient_count ?? 0);
                setVisiblePatientsCurrent(narrowed?.visible_patient_count ?? base.visible_patient_count ?? 0);
                setVisiblePatientsTrialCount(base.trial_count ?? 0);
            } catch (e: unknown) {
                if (cancelled) return;
                setVisiblePatientsBaseline(null);
                setVisiblePatientsCurrent(null);
                setVisiblePatientsTrialCount(0);
                setVisiblePatientsError(e instanceof Error ? e.message : 'Unable to compute visible patients');
            } finally {
                if (!cancelled) {
                    setVisiblePatientsLoading(false);
                }
            }
        }

        loadVisiblePatientsPreview();

        return () => {
            cancelled = true;
        };
    }, [assignForm.access_level, assignForm.cohort_id, assignForm.duration_days, assignForm.trial_id, assignmentScope, assignmentScopeOptions, showAssign]);

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
            const narrowedScopePayload = buildAssignmentScopePayload();
            await managerApi.assignResearcher({
                researcher_username: assignForm.researcher_username,
                trial_id: assignForm.trial_id || undefined,
                cohort_id: assignForm.cohort_id || undefined,
                access_level: assignForm.access_level,
                duration_days: assignForm.duration_days,
                assignment_scope: narrowedScopePayload,
            });
            setShowAssign(false);
            setAssignmentScope({ region: [], area: [], phase: [], purpose: [] });
            setVisiblePatientsBaseline(null);
            setVisiblePatientsCurrent(null);
            setVisiblePatientsTrialCount(0);
            setVisiblePatientsError('');
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
                        onClick={() => {
                            setAssignmentScope({ region: [], area: [], phase: [], purpose: [] });
                            setShowAssign(true);
                        }}
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
                            <div key={g.grant_id} className="bg-white rounded-lg shadow p-6 flex justify-between items-start gap-6">
                                <div>
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <h3 className="font-medium text-gray-900">{g.asset_title}</h3>
                                        <span className="text-xs bg-indigo-100 text-indigo-800 px-2 py-0.5 rounded-full">
                                            {buildRestrictionTags(g.scope).length} restriction{buildRestrictionTags(g.scope).length !== 1 ? 's' : ''}
                                        </span>
                                    </div>
                                    <p className="text-sm text-gray-500">
                                        Granted: {new Date(g.granted_at).toLocaleDateString()}
                                        {' · Expires: '}
                                        {new Date(g.expires_at).toLocaleDateString()}
                                    </p>
                                    <p className="text-sm text-gray-600 mt-1">
                                        Visible Patients Under Grant Scope: <span className="font-semibold">{g.permitted_patient_count ?? 0}</span>
                                    </p>
                                    <div className="mt-2 flex flex-wrap gap-1.5">
                                        {buildRestrictionTags(g.scope).length > 0 ? (
                                            buildRestrictionTags(g.scope).map((tag) => (
                                                <span key={`${g.grant_id}-${tag}`} className="text-xs bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded border border-indigo-100">
                                                    {tag}
                                                </span>
                                            ))
                                        ) : (
                                            <span className="text-xs text-gray-500">No additional restrictions</span>
                                        )}
                                    </div>
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
                                        <div className="mt-3">
                                            <p className="text-xs font-semibold text-gray-700 mb-1">Assigned Trials</p>
                                            {c.trial_ids.length > 0 ? (
                                                <div className="flex flex-wrap gap-1.5">
                                                    {c.trial_ids.map((trialId) => (
                                                        <span
                                                            key={`${c.cohort_id}-trial-${trialId}`}
                                                            className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded border border-blue-100"
                                                        >
                                                            {resolveTrialLabel(trialId)}
                                                        </span>
                                                    ))}
                                                </div>
                                            ) : (
                                                <p className="text-xs text-gray-500">No trials assigned</p>
                                            )}
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
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Visible Patients</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Expires</th>
                                <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                            </tr>
                        </thead>
                        <tbody className="divide-y divide-gray-200">
                            {assignments.map((a) => (
                                <tr key={a.assignment_id} className="hover:bg-gray-50">
                                    <td className="px-6 py-4 text-sm font-medium text-gray-900">{a.researcher_id}</td>
                                    <td className="px-6 py-4 text-sm text-gray-700">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <p>
                                            {a.trial_id
                                            ? `Trial: ${resolveTrialLabel(a.trial_id)}`
                                            : `Cohort: ${resolveCohortLabel(a.cohort_id)}`}
                                            </p>
                                            <span className="text-xs bg-indigo-100 text-indigo-800 px-2 py-0.5 rounded-full">
                                                {assignmentRestrictionTags(a).length} restriction{assignmentRestrictionTags(a).length !== 1 ? 's' : ''}
                                            </span>
                                        </div>
                                        <div className="mt-2 flex flex-wrap gap-1.5">
                                            {assignmentRestrictionTags(a).length > 0 ? (
                                                assignmentRestrictionTags(a).map((tag) => (
                                                    <span key={`${a.assignment_id}-${tag}`} className="text-xs bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded border border-indigo-100">
                                                        {tag}
                                                    </span>
                                                ))
                                            ) : (
                                                <span className="text-xs text-gray-500">No additional restrictions</span>
                                            )}
                                        </div>
                                    </td>
                                    <td className="px-6 py-4">
                                        <StatusBadge status={a.access_level === 'individual' ? 'sensitive' : 'standard'} />
                                        <span className="ml-2 text-sm text-gray-600">{a.access_level}</span>
                                    </td>
                                    <td className="px-6 py-4 text-sm text-gray-700 font-medium">
                                        {a.visible_patient_count ?? 0}
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
                                        onClick={() => {
                                            setAssignForm({ ...assignForm, trial_id: '', cohort_id: '' });
                                            setAssignmentScope({ region: [], area: [], phase: [], purpose: [] });
                                        }}
                                        className={`flex-1 py-2 text-sm font-medium transition-colors ${!assignForm.cohort_id && !assignForm.trial_id || assignForm.trial_id
                                            ? 'bg-blue-600 text-white'
                                            : 'bg-white text-gray-700 hover:bg-gray-50'
                                            }`}
                                    >
                                        🧪 Clinical Trial
                                    </button>
                                    <button
                                        onClick={() => {
                                            setAssignForm({ ...assignForm, trial_id: '', cohort_id: cohorts[0]?.cohort_id || '' });
                                            setAssignmentScope({ region: [], area: [], phase: [], purpose: [] });
                                        }}
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
                                            onChange={(e) => {
                                                setAssignForm({ ...assignForm, trial_id: e.target.value, cohort_id: '' });
                                                setAssignmentScope({ region: [], area: [], phase: [], purpose: [] });
                                            }}
                                            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-blue-500"
                                        >
                                            <option value="">Select a trial...</option>
                                            {grants.filter(g => g.is_active).map((g) => (
                                                <option key={g.grant_id} value={g.trial_id}>
                                                    {g.asset_title} ({shortId(g.trial_id)})
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
                                            onChange={(e) => {
                                                setAssignForm({ ...assignForm, cohort_id: e.target.value, trial_id: '' });
                                                setAssignmentScope({ region: [], area: [], phase: [], purpose: [] });
                                            }}
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

                            {(assignForm.trial_id || assignForm.cohort_id) && (
                                <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-900">
                                    {visiblePatientsLoading ? (
                                        <p>Calculating visible patients...</p>
                                    ) : visiblePatientsError ? (
                                        <p className="text-amber-700">{visiblePatientsError}</p>
                                    ) : (
                                        <div className="space-y-1">
                                            <p>
                                                Visible under current org ceiling: <span className="font-semibold">{visiblePatientsBaseline ?? 0}</span>
                                                {visiblePatientsTrialCount > 0 ? ` across ${visiblePatientsTrialCount} trial${visiblePatientsTrialCount !== 1 ? 's' : ''}` : ''}
                                            </p>
                                            <p>
                                                Visible after assignment filters: <span className="font-semibold">{visiblePatientsCurrent ?? visiblePatientsBaseline ?? 0}</span>
                                            </p>
                                        </div>
                                    )}
                                </div>
                            )}

                            {assignForm.access_level === 'individual' && (assignForm.trial_id || assignForm.cohort_id) && (
                                <div className="rounded-lg border border-indigo-200 bg-indigo-50 p-3 space-y-3">
                                    <div>
                                        <p className="text-sm font-medium text-indigo-900">Optional Assignment Scope (subset of org ceiling)</p>
                                        <p className="text-xs text-indigo-700">
                                            Ceiling-defined values are preselected and locked. For unconstrained dimensions, you can pick stricter values.
                                        </p>
                                        <div className="mt-2 flex flex-wrap gap-2 text-xs">
                                            <span className="inline-flex items-center gap-1 rounded border border-slate-300 bg-slate-200 px-2 py-0.5 text-slate-700">
                                                <Lock className="h-3 w-3" /> Ceiling locked
                                            </span>
                                            <span className="inline-flex items-center gap-1 rounded border border-indigo-300 bg-white px-2 py-0.5 text-indigo-700">
                                                Manager selectable
                                            </span>
                                        </div>
                                    </div>
                                    {([
                                        ['region', 'Regions'],
                                        ['area', 'Therapeutic Areas'],
                                        ['phase', 'Phases'],
                                        ['purpose', 'Purposes'],
                                    ] as Array<[AssignmentScopeKey, string]>).map(([key, label]) => (
                                        <div key={key}>
                                            <p className="text-xs font-semibold text-indigo-800 mb-1">{label}</p>
                                            {assignmentScopeOptions[key].totalTrials > 0 && (
                                                <p className="text-xs text-indigo-700 mb-1">
                                                    Derived from {assignmentScopeOptions[key].constrainedTrials} constrained trial{assignmentScopeOptions[key].constrainedTrials !== 1 ? 's' : ''}
                                                    {assignmentScopeOptions[key].totalTrials > assignmentScopeOptions[key].constrainedTrials
                                                        ? ` (${assignmentScopeOptions[key].totalTrials - assignmentScopeOptions[key].constrainedTrials} unconstrained)`
                                                        : ''}
                                                </p>
                                            )}
                                            {assignmentScopeOptions[key].options.length === 0 ? (
                                                <p className="text-xs text-indigo-600">No options available for this dimension</p>
                                            ) : (
                                                <div className="flex flex-wrap gap-2">
                                                    {assignmentScopeOptions[key].options.map((value) => {
                                                        const selected = assignmentScope[key].includes(value);
                                                        const isLocked = assignmentScopeOptions[key].constrained;
                                                        return (
                                                            <button
                                                                key={`${key}-${value}`}
                                                                type="button"
                                                                onClick={() => toggleScopeValue(key, value)}
                                                                disabled={isLocked}
                                                                className={`inline-flex items-center gap-1 text-xs px-2 py-1 rounded border ${isLocked
                                                                    ? 'bg-slate-200 text-slate-700 border-slate-300 cursor-not-allowed'
                                                                    : selected
                                                                        ? 'bg-indigo-600 text-white border-indigo-600'
                                                                        : 'bg-white text-indigo-700 border-indigo-300 hover:bg-indigo-100'
                                                                    }`}
                                                            >
                                                                {isLocked && <Lock className="h-3 w-3" />}
                                                                {value}
                                                            </button>
                                                        );
                                                    })}
                                                </div>
                                            )}
                                            {assignmentScopeOptions[key].constrained && assignmentScopeOptions[key].options.length > 0 && (
                                                <p className="mt-1 text-xs text-indigo-700">
                                                    Predefined by org ceiling: locked by default and cannot be removed.
                                                </p>
                                            )}
                                        </div>
                                    ))}
                                    <div className="text-xs text-indigo-700">
                                        Selected scope: {Object.entries(assignmentScope)
                                            .flatMap(([k, v]) => (v.length > 0 ? [`${k}=${v.join(', ')}`] : []))
                                            .join(' · ') || 'Full org-granted scope'}
                                    </div>
                                </div>
                            )}
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
