import { useEffect, useState, useRef } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import keycloak, { getUserProfile, UserProfile } from './keycloak';
import Layout from './components/Layout';
import DomainOwnerDashboard from './pages/DomainOwnerDashboard';
import ManagerDashboard from './pages/ManagerDashboard';
import ResearcherDashboard from './pages/ResearcherDashboard';
import Marketplace from './pages/Marketplace';
import CohortBuilder from './pages/CohortBuilder';

export default function App() {
    const [initialized, setInitialized] = useState(false);
    const [user, setUser] = useState<UserProfile | null>(null);
    const isInitialized = useRef(false);

    useEffect(() => {
        if (isInitialized.current) return;
        isInitialized.current = true;

        keycloak
            .init({ onLoad: 'login-required', checkLoginIframe: false, pkceMethod: 'S256' })
            .then((authenticated) => {
                if (authenticated) {
                    setUser(getUserProfile(keycloak));
                }
                setInitialized(true);
            })
            .catch((err) => {
                console.error('Keycloak init failed', err);
                setInitialized(true);
            });
    }, []);

    if (!initialized) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-gray-50">
                <div className="text-center">
                    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto" />
                    <p className="mt-4 text-gray-600">Authenticating...</p>
                </div>
            </div>
        );
    }

    if (!user) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-gray-50">
                <div className="bg-white p-8 rounded-lg shadow text-center">
                    <p className="text-red-600 font-medium">Authentication failed</p>
                    <button
                        onClick={() => keycloak.login()}
                        className="mt-4 px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700"
                    >
                        Try again
                    </button>
                </div>
            </div>
        );
    }

    const homeRoute = () => {
        switch (user.role) {
            case 'domain_owner': return '/owner';
            case 'manager': return '/manager';
            case 'researcher': return '/researcher';
        }
    };

    return (
        <Layout user={user}>
            <Routes>
                <Route path="/" element={<Navigate to={homeRoute()} replace />} />

                {/* Domain Owner routes */}
                <Route
                    path="/owner/*"
                    element={
                        user.role === 'domain_owner'
                            ? <DomainOwnerDashboard user={user} />
                            : <Navigate to="/" replace />
                    }
                />

                {/* Manager routes */}
                <Route
                    path="/manager/*"
                    element={
                        user.role === 'manager'
                            ? <ManagerDashboard user={user} />
                            : <Navigate to="/" replace />
                    }
                />
                <Route
                    path="/marketplace"
                    element={
                        user.role === 'manager'
                            ? <Marketplace user={user} />
                            : <Navigate to="/" replace />
                    }
                />
                <Route
                    path="/cohorts/new"
                    element={
                        user.role === 'manager'
                            ? <CohortBuilder user={user} />
                            : <Navigate to="/" replace />
                    }
                />

                {/* Researcher routes */}
                <Route
                    path="/researcher/*"
                    element={
                        user.role === 'researcher'
                            ? <ResearcherDashboard user={user} />
                            : <Navigate to="/" replace />
                    }
                />
            </Routes>
        </Layout>
    );
}