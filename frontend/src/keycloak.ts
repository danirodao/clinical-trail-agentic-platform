import Keycloak from 'keycloak-js';

const keycloak = new Keycloak({
    url: 'http://localhost:8180',
    realm: 'clinical-trials',
    clientId: 'research-platform-frontend',
});

export default keycloak;

export interface UserProfile {
    userId: string;
    username: string;
    email: string;
    role: 'domain_owner' | 'manager' | 'researcher';
    organizationId: string;
    organizationName: string;
}

export function getUserProfile(kc: Keycloak): UserProfile {
    const token = kc.tokenParsed as Record<string, unknown>;
    const realmRoles = (token?.realm_access as { roles?: string[] })?.roles ?? [];

    let role: UserProfile['role'] = 'researcher';
    if (realmRoles.includes('domain_owner')) role = 'domain_owner';
    else if (realmRoles.includes('manager')) role = 'manager';

    return {
        userId: token?.sub as string ?? '',
        username: token?.preferred_username as string ?? '',
        email: token?.email as string ?? '',
        role,
        organizationId: token?.organization_id as string ?? '',
        organizationName: token?.organization_name as string ?? '',
    };
}