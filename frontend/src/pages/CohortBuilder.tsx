import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { UserProfile } from '../keycloak';
import { managerApi, AccessGrant, CohortFilter, CohortPreview, FilterOptionsResponse } from '../api/client';
import {
    AlertTriangle, CheckCircle,
    Eye, Save, ArrowLeft
} from 'lucide-react';

interface Props { user: UserProfile }

const RESTRICTION_LABELS: Record<string, string> = {
    permitted_regions: 'Regions',
    regions: 'Regions',
    region: 'Regions',
    permitted_countries: 'Countries',
    countries: 'Countries',
    country: 'Country',
    permitted_areas: 'Therapeutic Areas',
    therapeutic_areas: 'Therapeutic Areas',
    areas: 'Therapeutic Areas',
    area: 'Therapeutic Area',
    permitted_phases: 'Phases',
    phases: 'Phases',
    phase: 'Phase',
    approved_purposes: 'Purposes',
    purposes: 'Purposes',
    purpose: 'Purpose',
    minimum_cohort_size: 'Min Cohort Size',
    resource_classification: 'Classification',
};

function formatInheritedRestrictions(scope?: Record<string, unknown>): string[] {
    if (!scope) return [];
    return Object.entries(scope).flatMap(([key, value]) => {
        if (value === null || value === undefined) return [];
        const label = RESTRICTION_LABELS[key]
            ?? key.split('_').map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');

        if (Array.isArray(value)) {
            const cleaned = value.map((v) => String(v).trim()).filter(Boolean);
            if (!cleaned.length) return [];
            return [`${label}: ${cleaned.join(', ')}`];
        }

        if (typeof value === 'boolean') {
            return [`${label}: ${value ? 'Yes' : 'No'}`];
        }

        if (typeof value === 'string' || typeof value === 'number') {
            const text = String(value).trim();
            if (!text) return [];
            return [`${label}: ${text}`];
        }
        return [];
    });
}

function formatPatientFilterSummary(filter: CohortFilter): string[] {
    const out: string[] = [];

    if (filter.age_min !== undefined || filter.age_max !== undefined) {
        out.push(`Age: ${filter.age_min ?? 0}-${filter.age_max ?? 120}`);
    }
    if (filter.sex?.length) out.push(`Sex: ${filter.sex.join(', ')}`);
    if (filter.conditions?.length) out.push(`Conditions: ${filter.conditions.join(', ')}`);
    if (filter.phases?.length) out.push(`Phases: ${filter.phases.join(', ')}`);
    if (filter.country?.length) out.push(`Countries: ${filter.country.join(', ')}`);
    if (filter.ethnicity?.length) out.push(`Ethnicity: ${filter.ethnicity.join(', ')}`);
    if (filter.disposition_status?.length) out.push(`Status: ${filter.disposition_status.join(', ')}`);
    if (filter.arm_assigned?.length) out.push(`Arm: ${filter.arm_assigned.join(', ')}`);

    return out;
}

export default function CohortBuilder(_: Props) {
    const navigate = useNavigate();
    const [grants, setGrants] = useState<AccessGrant[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState('');
    const [saving, setSaving] = useState(false);

    // Cohort definition
    const [name, setName] = useState('');
    const [description, setDescription] = useState('');
    const [isDynamic, setIsDynamic] = useState(true);
    const [filter, setFilter] = useState<CohortFilter>({
        trial_ids: [],
        therapeutic_areas: [],
        conditions: [],
        age_min: undefined,
        age_max: undefined,
        sex: [],
        phases: [],
    });

    // Preview
    const [preview, setPreview] = useState<CohortPreview | null>(null);
    const [previewing, setPreviewing] = useState(false);

    // Dynamic Filter Options
    const [filterOptions, setFilterOptions] = useState<FilterOptionsResponse | null>(null);

    useEffect(() => {
        Promise.all([
            managerApi.listMyOrgGrants(),
            managerApi.getFilterOptions()
        ]).then(([grantsRes, optionsRes]) => {
            setGrants(grantsRes.grants);
            setFilterOptions(optionsRes);
            setLoading(false);
        }).catch(err => {
            setError(err instanceof Error ? err.message : 'Failed to load initial data');
            setLoading(false);
        });
    }, []);

    function toggleTrialId(trialId: string) {
        setFilter((f) => ({
            ...f,
            trial_ids: f.trial_ids?.includes(trialId)
                ? f.trial_ids.filter((id) => id !== trialId)
                : [...(f.trial_ids || []), trialId],
        }));
        setPreview(null); // Reset preview on change
    }

    function toggleArrayField(field: keyof CohortFilter, value: string) {
        setFilter((f) => {
            const arr = (f[field] as string[]) || [];
            return {
                ...f,
                [field]: arr.includes(value) ? arr.filter((v) => v !== value) : [...arr, value],
            };
        });
        setPreview(null);
    }

    async function handlePreview() {
        setPreviewing(true);
        setError('');
        try {
            const result = await managerApi.previewCohort({
                name: name || 'Preview',
                filter_criteria: filter,
                is_dynamic: isDynamic,
            });
            setPreview(result);
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Preview failed');
        } finally {
            setPreviewing(false);
        }
    }

    async function handleSave() {
        if (!name.trim()) { setError('Cohort name is required'); return; }
        setSaving(true);
        try {
            await managerApi.createCohort({
                name,
                description,
                filter_criteria: filter,
                is_dynamic: isDynamic,
            });
            navigate('/manager');
        } catch (e: unknown) {
            setError(e instanceof Error ? e.message : 'Save failed');
        } finally {
            setSaving(false);
        }
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center py-20">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600" />
            </div>
        );
    }

    const selectedGrants = grants.filter((g) => filter.trial_ids?.includes(g.trial_id));
    const patientSummary = formatPatientFilterSummary(filter);
    const perTrialEffectiveSummary = selectedGrants.map((grant) => {
        const inherited = formatInheritedRestrictions(grant.scope);
        const combined = Array.from(new Set([...inherited, ...patientSummary]));
        return {
            grantId: grant.grant_id,
            title: grant.asset_title,
            inherited,
            patient: patientSummary,
            combined,
        };
    });

    return (
        <div>
            <button
                onClick={() => navigate('/manager')}
                className="flex items-center space-x-1 text-sm text-gray-500 hover:text-gray-700 mb-4"
            >
                <ArrowLeft className="h-4 w-4" />
                <span>Back to Dashboard</span>
            </button>

            <div className="flex justify-between items-start mb-8">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Cohort Builder</h1>
                    <p className="mt-1 text-sm text-gray-500">
                        Define patient subsets from your organization's accessible trials
                    </p>
                </div>
            </div>

            {error && (
                <div className="mb-4 p-4 bg-red-50 border border-red-200 rounded-lg flex items-center space-x-2">
                    <AlertTriangle className="h-5 w-5 text-red-500" />
                    <span className="text-red-700">{error}</span>
                    <button onClick={() => setError('')} className="ml-auto text-red-500">&times;</button>
                </div>
            )}

            <div className="grid grid-cols-3 gap-6">
                {/* Left: Filters */}
                <div className="col-span-2 space-y-6">
                    {/* Cohort metadata */}
                    <div className="bg-white rounded-lg shadow p-6">
                        <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-4">
                            Cohort Details
                        </h2>
                        <div className="grid grid-cols-2 gap-4">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                                <input
                                    type="text"
                                    value={name}
                                    onChange={(e) => setName(e.target.value)}
                                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                    placeholder="e.g. Oncology Elderly HF Comorbidity"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-1">Type</label>
                                <div className="flex space-x-4 mt-2">
                                    <label className="flex items-center space-x-2 cursor-pointer">
                                        <input
                                            type="radio"
                                            checked={isDynamic}
                                            onChange={() => setIsDynamic(true)}
                                            className="text-blue-600"
                                        />
                                        <span className="text-sm">Dynamic (auto-updates)</span>
                                    </label>
                                    <label className="flex items-center space-x-2 cursor-pointer">
                                        <input
                                            type="radio"
                                            checked={!isDynamic}
                                            onChange={() => setIsDynamic(false)}
                                            className="text-blue-600"
                                        />
                                        <span className="text-sm">Static (snapshot)</span>
                                    </label>
                                </div>
                            </div>
                        </div>
                        <div className="mt-4">
                            <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                            <textarea
                                value={description}
                                onChange={(e) => setDescription(e.target.value)}
                                rows={2}
                                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                placeholder="Purpose and criteria for this cohort..."
                            />
                        </div>
                    </div>

                    {/* Trial selection (from ceiling) */}
                    <div className="bg-white rounded-lg shadow p-6">
                        <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-4">
                            Select Trials
                            <span className="ml-2 text-xs font-normal text-gray-500">
                                (only trials your organization has access to)
                            </span>
                        </h2>
                        {grants.length === 0 ? (
                            <p className="text-sm text-gray-500">
                                No trial access grants. Request access from the marketplace first.
                            </p>
                        ) : (
                            <div className="space-y-2">
                                {grants.map((g) => (
                                    <label
                                        key={g.grant_id}
                                        className={`flex items-center space-x-3 p-3 rounded-lg border cursor-pointer transition-colors ${filter.trial_ids?.includes(g.trial_id)
                                            ? 'border-blue-500 bg-blue-50'
                                            : 'border-gray-200 hover:bg-gray-50'
                                            }`}
                                    >
                                        <input
                                            type="checkbox"
                                            checked={filter.trial_ids?.includes(g.trial_id) || false}
                                            onChange={() => toggleTrialId(g.trial_id)}
                                            className="h-4 w-4 text-blue-600 rounded"
                                        />
                                        <div className="flex-1">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <p className="text-sm font-medium text-gray-900">{g.asset_title}</p>
                                                <span className="text-xs bg-indigo-100 text-indigo-800 px-2 py-0.5 rounded-full">
                                                    {formatInheritedRestrictions(g.scope).length} inherited restriction{formatInheritedRestrictions(g.scope).length !== 1 ? 's' : ''}
                                                </span>
                                            </div>
                                            <p className="text-xs text-gray-500">
                                                Expires: {new Date(g.expires_at).toLocaleDateString()}
                                            </p>
                                        </div>
                                    </label>
                                ))}
                            </div>
                        )}

                        {selectedGrants.length > 0 && (
                            <div className="mt-4 rounded-lg border border-indigo-200 bg-indigo-50 p-4">
                                <p className="text-xs font-semibold uppercase tracking-wide text-indigo-800 mb-2">
                                    Inherited Trial Restrictions
                                </p>
                                <p className="text-xs text-indigo-700 mb-3">
                                    These are enforced from your org ceiling. Patient filters below are applied on top.
                                </p>
                                <div className="space-y-3">
                                    {selectedGrants.map((g) => {
                                        const tags = formatInheritedRestrictions(g.scope);
                                        return (
                                            <div key={`inherited-${g.grant_id}`}>
                                                <p className="text-sm font-medium text-indigo-900">{g.asset_title}</p>
                                                <div className="mt-1 flex flex-wrap gap-1.5">
                                                    {tags.length > 0 ? (
                                                        tags.map((tag) => (
                                                            <span key={`${g.grant_id}-${tag}`} className="text-xs bg-white text-indigo-700 px-2 py-0.5 rounded border border-indigo-200">
                                                                {tag}
                                                            </span>
                                                        ))
                                                    ) : (
                                                        <span className="text-xs text-indigo-700">No additional inherited restrictions</span>
                                                    )}
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Patient filters */}
                    <div className="bg-white rounded-lg shadow p-6">
                        <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wider mb-4">
                            Patient Filters
                        </h2>
                        <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 p-3">
                            <p className="text-xs text-blue-800">
                                Selected patient filters are applied on top of inherited trial restrictions (ceiling), never instead of them.
                            </p>
                        </div>
                        <div className="grid grid-cols-2 gap-6">
                            {/* Age range */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">Age Range</label>
                                <div className="flex items-center space-x-2">
                                    <input
                                        type="number"
                                        value={filter.age_min ?? ''}
                                        onChange={(e) => setFilter({ ...filter, age_min: e.target.value ? Number(e.target.value) : undefined })}
                                        placeholder="Min"
                                        className="w-24 border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                    />
                                    <span className="text-gray-400">—</span>
                                    <input
                                        type="number"
                                        value={filter.age_max ?? ''}
                                        onChange={(e) => setFilter({ ...filter, age_max: e.target.value ? Number(e.target.value) : undefined })}
                                        placeholder="Max"
                                        className="w-24 border border-gray-300 rounded-lg px-3 py-2 text-sm"
                                    />
                                </div>
                            </div>

                            {/* Sex */}
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-2">Sex</label>
                                <div className="flex space-x-3">
                                    {['M', 'F'].map((s) => (
                                        <label key={s} className="flex items-center space-x-2 cursor-pointer">
                                            <input
                                                type="checkbox"
                                                checked={filter.sex?.includes(s) || false}
                                                onChange={() => toggleArrayField('sex', s)}
                                                className="h-4 w-4 text-blue-600 rounded"
                                            />
                                            <span className="text-sm">{s}</span>
                                        </label>
                                    ))}
                                </div>
                            </div>

                            {/* Conditions */}
                            <div className="col-span-2">
                                <label className="block text-sm font-medium text-gray-700 mb-2">Conditions</label>
                                <div className="flex flex-wrap gap-2">
                                    {(filterOptions?.conditions || []).map((cond) => (
                                        <button
                                            key={cond}
                                            onClick={() => toggleArrayField('conditions', cond)}
                                            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${filter.conditions?.includes(cond)
                                                ? 'bg-blue-600 text-white'
                                                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                                }`}
                                        >
                                            {cond}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {/* Phases */}
                            {/* <div className="col-span-2">
                                <label className="block text-sm font-medium text-gray-700 mb-2">Trial Phases</label>
                                <div className="flex flex-wrap gap-2">
                                    {['Phase 1', 'Phase 2', 'Phase 3', 'Phase 4'].map((phase) => (
                                        <button
                                            key={phase}
                                            onClick={() => toggleArrayField('phases', phase)}
                                            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${filter.phases?.includes(phase)
                                                ? 'bg-blue-600 text-white'
                                                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                                }`}
                                        >
                                            {phase}
                                        </button>
                                    ))}
                                </div>
                            </div>*/}

                            {/* Country */}
                            <div className="col-span-2">
                                <label className="block text-sm font-medium text-gray-700 mb-2">Country</label>
                                <div className="flex flex-wrap gap-2">
                                    {(filterOptions?.country || []).map((country) => (
                                        <button
                                            key={country}
                                            onClick={() => toggleArrayField('country', country)}
                                            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${filter.country?.includes(country)
                                                ? 'bg-blue-600 text-white'
                                                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                                }`}
                                        >
                                            {country}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {/* Ethnicity */}
                            <div className="col-span-2">
                                <label className="block text-sm font-medium text-gray-700 mb-2">Ethnicity</label>
                                <div className="flex flex-wrap gap-2">
                                    {(filterOptions?.ethnicity || []).map((ethnicity) => (
                                        <button
                                            key={ethnicity}
                                            onClick={() => toggleArrayField('ethnicity', ethnicity)}
                                            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${filter.ethnicity?.includes(ethnicity)
                                                ? 'bg-blue-600 text-white'
                                                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                                }`}
                                        >
                                            {ethnicity}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {/* Disposition Status */}
                            <div className="col-span-2">
                                <label className="block text-sm font-medium text-gray-700 mb-2">Disposition Status</label>
                                <div className="flex flex-wrap gap-2">
                                    {(filterOptions?.disposition_status || []).map((ds) => (
                                        <button
                                            key={ds}
                                            onClick={() => toggleArrayField('disposition_status', ds)}
                                            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${filter.disposition_status?.includes(ds)
                                                ? 'bg-blue-600 text-white'
                                                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                                }`}
                                        >
                                            {ds}
                                        </button>
                                    ))}
                                </div>
                            </div>

                            {/* Arm Assigned */}
                            <div className="col-span-2">
                                <label className="block text-sm font-medium text-gray-700 mb-2">Arm Assigned</label>
                                <div className="flex flex-wrap gap-2">
                                    {(filterOptions?.arm_assigned || []).map((arm) => (
                                        <button
                                            key={arm}
                                            onClick={() => toggleArrayField('arm_assigned', arm)}
                                            className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${filter.arm_assigned?.includes(arm)
                                                ? 'bg-blue-600 text-white'
                                                : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
                                                }`}
                                        >
                                            {arm}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>

                {/* Right: Preview panel */}
                <div className="space-y-6">
                    {/* Actions */}
                    <div className="bg-white rounded-lg shadow p-6 sticky top-24">
                        <div className="space-y-3">
                            <button
                                onClick={handlePreview}
                                disabled={!filter.trial_ids?.length || previewing}
                                className="w-full flex items-center justify-center space-x-2 py-2.5 bg-gray-100 text-gray-700 rounded-lg hover:bg-gray-200 text-sm font-medium disabled:opacity-50"
                            >
                                <Eye className="h-4 w-4" />
                                <span>{previewing ? 'Loading...' : 'Preview Cohort'}</span>
                            </button>
                            <button
                                onClick={handleSave}
                                disabled={!name.trim() || !filter.trial_ids?.length || saving}
                                className="w-full flex items-center justify-center space-x-2 py-2.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 text-sm font-medium disabled:opacity-50"
                            >
                                <Save className="h-4 w-4" />
                                <span>{saving ? 'Saving...' : 'Save Cohort'}</span>
                            </button>
                        </div>

                        {/* Preview results */}
                        {preview && (
                            <div className="mt-6 space-y-4">
                                {/* Effective final filter summary */}
                                <div className="rounded-lg border border-blue-200 bg-blue-50 p-3">
                                    <p className="text-xs font-semibold text-blue-900 uppercase tracking-wide mb-2">
                                        Effective Final Filters Per Trial (AND)
                                    </p>
                                    <p className="text-[11px] text-blue-800 mb-3">
                                        Cohort filters are always applied on top of inherited ceiling restrictions and can never override them.
                                    </p>

                                    <div className="space-y-3">
                                        {perTrialEffectiveSummary.map((item) => (
                                            <div key={`effective-${item.grantId}`} className="rounded border border-blue-200 bg-white p-2.5">
                                                <p className="text-xs font-medium text-blue-900">{item.title}</p>

                                                <div className="mt-2">
                                                    <p className="text-[11px] font-medium text-blue-800">Inherited</p>
                                                    <div className="mt-1 flex flex-wrap gap-1.5">
                                                        {item.inherited.length > 0 ? (
                                                            item.inherited.map((tag) => (
                                                                <span key={`inh-${item.grantId}-${tag}`} className="text-[11px] bg-blue-50 text-blue-800 px-2 py-0.5 rounded border border-blue-200">
                                                                    {tag}
                                                                </span>
                                                            ))
                                                        ) : (
                                                            <span className="text-[11px] text-blue-700">None</span>
                                                        )}
                                                    </div>
                                                </div>

                                                <div className="mt-2">
                                                    <p className="text-[11px] font-medium text-blue-800">Patient</p>
                                                    <div className="mt-1 flex flex-wrap gap-1.5">
                                                        {item.patient.length > 0 ? (
                                                            item.patient.map((tag) => (
                                                                <span key={`pat-${item.grantId}-${tag}`} className="text-[11px] bg-blue-50 text-blue-800 px-2 py-0.5 rounded border border-blue-200">
                                                                    {tag}
                                                                </span>
                                                            ))
                                                        ) : (
                                                            <span className="text-[11px] text-blue-700">None</span>
                                                        )}
                                                    </div>
                                                </div>

                                                <div className="mt-2">
                                                    <p className="text-[11px] font-medium text-blue-800">Combined Result</p>
                                                    <div className="mt-1 flex flex-wrap gap-1.5">
                                                        {item.combined.length > 0 ? (
                                                            item.combined.map((tag) => (
                                                                <span key={`eff-${item.grantId}-${tag}`} className="text-[11px] bg-blue-100 text-blue-900 px-2 py-0.5 rounded border border-blue-300">
                                                                    {tag}
                                                                </span>
                                                            ))
                                                        ) : (
                                                            <span className="text-[11px] text-blue-700">No extra filters (trial selection only)</span>
                                                        )}
                                                    </div>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>

                                {/* Ceiling check */}
                                {preview.within_ceiling ? (
                                    <div className="flex items-center space-x-2 p-3 bg-green-50 rounded-lg">
                                        <CheckCircle className="h-5 w-5 text-green-600" />
                                        <span className="text-sm text-green-700 font-medium">
                                            Within organization ceiling
                                        </span>
                                    </div>
                                ) : (
                                    <div className="p-3 bg-red-50 rounded-lg">
                                        <div className="flex items-center space-x-2">
                                            <AlertTriangle className="h-5 w-5 text-red-600" />
                                            <span className="text-sm text-red-700 font-medium">
                                                Ceiling violation
                                            </span>
                                        </div>
                                        <ul className="mt-2 text-xs text-red-600 list-disc list-inside">
                                            {preview.ceiling_violations.map((v, i) => (
                                                <li key={i}>{v}</li>
                                            ))}
                                        </ul>
                                    </div>
                                )}

                                {/* Counts */}
                                <div className="grid grid-cols-2 gap-3">
                                    <div className="bg-gray-50 rounded-lg p-3 text-center">
                                        <p className="text-2xl font-bold text-gray-900">{preview.patient_count}</p>
                                        <p className="text-xs text-gray-500">Patients</p>
                                    </div>
                                    <div className="bg-gray-50 rounded-lg p-3 text-center">
                                        <p className="text-2xl font-bold text-gray-900">{preview.trial_count}</p>
                                        <p className="text-xs text-gray-500">Trials</p>
                                    </div>
                                </div>

                                {/* Demographics */}
                                {preview.demographics && (
                                    <div>
                                        <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">
                                            Demographics
                                        </h3>
                                        <div className="space-y-2">
                                            {Object.entries(preview.demographics.sex).map(([sex, count]) => (
                                                <div key={sex} className="flex justify-between text-sm">
                                                    <span className="text-gray-600">{sex}</span>
                                                    <span className="font-medium">{count as number}</span>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}

                                {/* Trials breakdown */}
                                {preview.trials && (
                                    <div>
                                        <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2">
                                            Trials
                                        </h3>
                                        <div className="space-y-1">
                                            {preview.trials.map((t) => (
                                                <div key={t.trial_id} className="flex justify-between text-xs">
                                                    <span className="text-gray-600 truncate pr-2">{t.title}</span>
                                                    <span className="font-medium whitespace-nowrap">{t.patient_count}p</span>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}