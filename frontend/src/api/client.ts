import keycloak from '../keycloak';

const API_BASE = '/api/v1';

async function getHeaders(): Promise<HeadersInit> {
    // Refresh token if expiring within 30s
    try {
        await keycloak.updateToken(30);
    } catch {
        keycloak.login();
    }

    return {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${keycloak.token}`,
    };
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const headers = await getHeaders();
    const resp = await fetch(`${API_BASE}${path}`, { ...options, headers });

    if (resp.status === 401) {
        keycloak.login();
        throw new Error('Unauthorized');
    }

    if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed: ${resp.status}`);
    }

    return resp.json();
}

// ─── Domain Owner API ────────────────────────────────────────

export const domainOwnerApi = {
    // Dashboard
    getStats: () => request<DashboardStats>('/dashboard/stats'),

    // Filter options for publish wizard
    getFilterOptions: () => request<FilterOptions>('/assets/filter-options'),

    // Discover trials matching criteria (preview)
    discoverTrials: (filters: TrialFilter) =>
        request<DiscoveryResult>('/assets/discover', {
            method: 'POST',
            body: JSON.stringify(filters),
        }),

    // Publish a collection
    publishCollection: (data: PublishCollectionInput) =>
        request<PublishResult>('/assets/publish-collection', {
            method: 'POST',
            body: JSON.stringify(data),
        }),

    // Refresh dynamic collections
    refreshCollections: () =>
        request('/assets/refresh-collections', { method: 'POST' }),

    // List my collections
    listCollections: () =>
        request<{ collections: Collection[] }>('/assets/'),

    // Pending requests
    listPendingRequests: () =>
        request<{ requests: PendingRequest[] }>('/access-requests/pending'),

    // Review request
    reviewRequest: (requestId: string, data: ReviewAction) =>
        request(`/access-requests/${requestId}/review`, {
            method: 'POST',
            body: JSON.stringify(data),
        }),

    // Grants
    listGrants: () =>
        request<{ grants: CollectionGrant[] }>('/grants/'),

    revokeGrant: (collectionId: string, orgId: string, reason: string) =>
        request(`/grants/${collectionId}/revoke/${orgId}`, {
            method: 'POST',
            body: JSON.stringify({ reason }),
        }),
};


// ─── Manager API ─────────────────────────────────────────────

export const managerApi = {
    browseMarketplace: () =>
        request<{ collections: MarketplaceCollection[] }>('/marketplace/'),

    getCollectionDetail: (id: string) =>
        request<CollectionDetail>(`/marketplace/${id}`),

    requestAccess: (data: { collection_id: string; justification: string; requested_duration_days: number }) =>
        request('/marketplace/request-access', {
            method: 'POST',
            body: JSON.stringify(data),
        }),

    listMyOrgGrants: () =>
        request<{ grants: AccessGrant[] }>('/my-org/grants'),

    listCohorts: () =>
        request<{ cohorts: Cohort[] }>('/cohorts/'),

    previewCohort: (data: CohortCreate) =>
        request<CohortPreview>('/cohorts/preview', {
            method: 'POST',
            body: JSON.stringify(data),
        }),

    createCohort: (data: CohortCreate) =>
        request<{ cohort_id: string }>('/cohorts/', {
            method: 'POST',
            body: JSON.stringify(data),
        }),

    assignResearcher: (data: AssignResearcherRequest) =>
        request('/assignments/', {
            method: 'POST',
            body: JSON.stringify(data),
        }),

    listAssignments: () =>
        request<{ assignments: Assignment[] }>('/assignments/'),

    getFilterOptions: () =>
        request<FilterOptionsResponse>('/cohorts/filter-options'),
};

export interface FilterOptionsResponse {
    conditions: string[];
    country: string[];
    ethnicity: string[];
    disposition_status: string[];
    arm_assigned: string[];
}

// ─── Researcher API ──────────────────────────────────────────

export const researcherApi = {
    getMyAccess: () =>
        request<AccessSummary>('/research/my-access'),

    query: (q: string) =>
        request('/research/query', {
            method: 'POST',
            body: JSON.stringify({ query: q }),


        }),

    queryStream: (req: QueryRequest) =>
        fetchStream('/research/query/stream', req),
    queryStream1: async (q: string, trialIds?: string[], sessionId?: string) => {
        const token = keycloak.token
        if (!token) {
            throw new Error("Not authenticated. Please log in.");
        }
        const response = await fetch(`${API_BASE}/research/query/stream/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                query: q,
                trial_ids: trialIds ?? null,     // Convert undefined to null
                session_id: sessionId ?? null    // Convert undefined to null
            })
        });

        if (!response.ok) {
            let errorMsg = `HTTP Error ${response.status}`;
            try {
                const errorJson = await response.json();
                errorMsg = errorJson.detail || errorJson.error || errorMsg;
            } catch (e) { }
            throw new Error(errorMsg);
        }

        if (!response.body) {
            throw new Error('Response body is null');
        }

        return response.body.getReader();
    },

    getHistory: (sessionId: string) =>
        request(`/research/conversations/${sessionId}`)


};



// ─── Types ───────────────────────────────────────────────────
export interface TrialFilter {
    therapeutic_areas?: string[];
    phases?: string[];
    study_types?: string[];
    regions?: string[];
    countries?: string[];
    overall_statuses?: string[];
    min_enrollment?: number;
    lead_sponsors?: string[];
}
export interface FilterOptions {
    therapeutic_areas: string[];
    phases: string[];
    study_types: string[];
    overall_statuses: string[];
    lead_sponsors: string[];
    regions: string[];
    countries: string[];
    stats: { total_trials: number; published_trials: number; unpublished_trials: number };
}

export interface Asset {
    asset_id: string;
    asset_type: string;
    reference_id: string;
    title: string;
    description?: string;
    sensitivity_level: string;
    therapeutic_area?: string;
    published_at: string;
    is_active: boolean;
}
export interface DiscoveredTrial {
    trial_id: string;
    nct_id: string;
    title: string;
    phase: string;
    therapeutic_area: string;
    overall_status: string;
    study_type: string;
    enrollment_count: number;
    patient_count: number;
    drug_names: string[];
    condition_names: string[];
    regions: string[];
    countries: string[];
    already_published: boolean;
}

export interface DiscoveryResult {
    total_matching: number;
    already_published: number;
    available_to_publish: number;
    trials: DiscoveredTrial[];
    summary: {
        therapeutic_areas: string[];
        phases: string[];
        total_patients: number;
        total_enrollment: number;
        drugs: string[];
        conditions: string[];
    };
}

export interface PublishCollectionInput {
    name: string;
    description: string;
    filter_criteria: TrialFilter;
    sensitivity_level: string;
    is_dynamic: boolean;
}

export interface PublishResult {
    collection_id: string;
    total_trials: number;
    newly_published: number;
    already_published: number;
    status: string;
}
export interface Collection {
    collection_id: string;
    name: string;
    description: string;
    sensitivity_level: string;
    is_dynamic: boolean;
    trial_count: number;
    total_patients: number;
    total_enrollment: number;
    therapeutic_areas: string[];
    phases: string[];
    regions: string[];
    countries: string[];
    filter_criteria: TrialFilter;
    organizations_with_access: number;
    pending_requests: number;
    created_at: string;
}

export interface MarketplaceCollection {
    collection_id: string;
    name: string;
    description: string;
    sensitivity_level: string;
    is_dynamic: boolean;
    trial_count: number;
    total_patients: number;
    total_enrollment: number;
    therapeutic_areas: string[];
    phases: string[];
    regions: string[];
    countries: string[];
    drug_names: string[];
    condition_names: string[];
    access_status: 'full_access' | 'partial_access' | 'pending' | 'no_access';
    created_at: string;
}

export interface CollectionDetail {
    collection: Collection;
    trials: DiscoveredTrial[];
    access: {
        granted_count: number;
        total_count: number;
        has_full_access: boolean;
        pending_request: { request_id: string; created_at: string } | null;
    };
}

export interface PendingRequest {
    request_id: string;
    collection_id: string;
    collection_name: string;
    requesting_org_id: string;
    justification: string;
    trial_count: number;
    therapeutic_areas: string[];
    phases: string[];
    total_patients: number;
    sensitivity_level: string;
    created_at: string;
}

export interface CollectionGrant {
    collection_id: string;
    collection_name: string;
    organization_id: string;
    trial_count: number;
    first_granted: string;
    earliest_expiry: string;
    all_active: boolean;
}

export interface DashboardStats {
    total_trials_in_system: number;
    published_trials: number;
    unpublished_trials: number;
    collections: number;
    pending_requests: number;
    organizations_with_access: number;
}

export interface ReviewAction {
    action: 'approve' | 'reject';
    notes?: string;
    grant_duration_days?: number;
}

export interface PublishAssetRequest {
    asset_type: string;
    reference_id: string;
    title: string;
    description?: string;
    sensitivity_level: string;
    therapeutic_area?: string;
}

export interface AccessRequest {
    request_id: string;
    asset_id: string;
    asset_title: string;
    asset_type: string;
    requesting_org_id: string;
    justification: string;
    scope: Record<string, unknown>;
    created_at: string;
    status: string;
}

export interface ReviewAction {
    action: 'approve' | 'reject';
    notes?: string;
    grant_duration_days?: number;
}

export interface AccessGrant {
    grant_id: string;
    asset_id: string;
    trial_id: string;
    asset_title: string;
    asset_type: string;
    organization_id: string;
    scope: Record<string, unknown>;
    granted_at: string;
    expires_at: string;
    is_active: boolean;
}

export interface MarketplaceAsset extends Asset {
    owner_name?: string;
    trial_phase?: string;
    enrollment_count?: number;
    conditions?: string[];
    already_requested: boolean;
    already_granted: boolean;
}

export interface AccessRequestCreate {
    asset_id: string;
    justification: string;
    scope?: Record<string, unknown>;
    requested_duration_days?: number;
}

export interface Cohort {
    cohort_id: string;
    name: string;
    description?: string;
    filter_criteria: CohortFilter;
    trial_ids: string[];
    patient_count: number;
    is_dynamic: boolean;
    created_at: string;
    assignments: CohortAssignment[];
}

export interface CohortFilter {
    trial_ids?: string[];
    therapeutic_areas?: string[];
    conditions?: string[];
    age_min?: number;
    age_max?: number;
    sex?: string[];
    phases?: string[];
    country?: string[];
    ethnicity?: string[];
    disposition_status?: string[];
    arm_assigned?: string[];
    has_adverse_events?: boolean;
    severity_levels?: string[];
}

export interface CohortCreate {
    name: string;
    description?: string;
    filter_criteria: CohortFilter;
    is_dynamic: boolean;
}

export interface CohortPreview {
    patient_count: number;
    trial_count: number;
    trials: { trial_id: string; title: string; patient_count: number }[];
    demographics: { sex: Record<string, number>; age_distribution: Record<string, number> };
    within_ceiling: boolean;
    ceiling_violations: string[];
}

export interface AssignResearcherRequest {
    researcher_username: string;
    trial_id?: string;
    cohort_id?: string;
    access_level: 'individual' | 'aggregate';
    duration_days: number;
}

export interface Assignment {
    assignment_id: string;
    researcher_id: string;
    trial_id?: string;
    cohort_id?: string;
    access_level: string;
    assigned_at: string;
    expires_at: string;
    is_active: boolean;
}

export interface CohortFilterCriteria {
    sex?: string[];
    phases?: string[];
    age_max?: number;
    age_min?: number;
    ethnicity?: string[];
    trial_ids?: string[];
    conditions?: string[];
    therapeutic_areas?: string[];
    country?: string[];
    disposition_status?: string[];
    arm_assigned?: string[];
}

export interface TrialCohortFilter {
    cohort_id: string;
    cohort_name: string;
    filter_criteria: CohortFilterCriteria;
}

export interface TrialAccess {
    trial_id: string;
    nct_id: string;
    title: string;
    phase: string;
    therapeutic_area: string;
    overall_status: string;
    enrollment_count: number;
    patient_count: number;
    access_level: 'individual' | 'aggregate';
    is_unrestricted: boolean;
    cohort_filters: TrialCohortFilter[];
}

export interface AccessSummary {
    user_id: string;
    username: string;
    role: string;
    organization_id: string;
    access_summary: {
        has_any_access: boolean;
        aggregate_only: boolean;
        aggregate_trial_count: number;
        individual_trial_count: number;
        aggregate_trial_ids: string[];
        individual_trial_ids: string[];
    };
    trial_access: TrialAccess[];           // ← THIS WAS MISSING
}
export interface QueryRequest {
    query: string;
    trial_ids?: string[];
    session_id?: string;
}

export interface ToolCallRecord {
    tool: string;
    args: Record<string, any>;
    result_summary: string;
    duration_ms: number;
    status: 'success' | 'error';
    error_message?: string;
}

export interface QuerySource {
    trial_id: string;
    nct_id: string;
    title: string;
}

export interface QueryMetadata {
    model_used: string;
    total_tokens: number;
    prompt_tokens: number;
    completion_tokens: number;
    duration_ms: number;
    iteration_count: number;
}

export interface QueryResponse {
    answer: string;
    sources: QuerySource[];
    tool_calls: ToolCallRecord[];
    access_level_applied: 'individual' | 'aggregate' | 'mixed' | 'none';
    filters_applied: string[];
    metadata: QueryMetadata;
    error?: string;
}

// Streaming Event Types (NDJSON)
export type StreamEvent =
    | { event: 'status'; data: { message: string } }
    | { event: 'tool_call'; data: { tool: string; args: any } }
    | { event: 'tool_result'; data: { tool: string; summary: string; duration_ms: number; status: string } }
    | { event: 'answer_token'; data: { token: string } }
    | { event: 'complete'; data: QueryResponse }
    | { event: 'error'; data: { message: string } };

// ─────────────────────────────────────────────────────────────────────────────
// Update researcherApi
// ─────────────────────────────────────────────────────────────────────────────

// Add this alongside your existing request() helper.
// It handles fetching a ReadableStream and passing the JWT.
async function fetchStream(endpoint: string, body: any): Promise<ReadableStreamDefaultReader<Uint8Array>> {
    const token = keycloak.token

    const response = await fetch(`${API_BASE}${endpoint}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(body)
    });

    if (!response.ok) {
        let errorMsg = `HTTP Error ${response.status}`;
        try {
            const errorJson = await response.json();
            errorMsg = errorJson.detail || errorJson.error || errorMsg;
        } catch (e) { }
        throw new Error(errorMsg);
    }

    if (!response.body) {
        throw new Error('Response body is null');
    }

    return response.body.getReader();
}



