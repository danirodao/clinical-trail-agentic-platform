import { ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { UserProfile } from '../keycloak';
import keycloak from '../keycloak';
import {
    Shield, Database, ShoppingBag, FlaskConical,
    LogOut, Home
} from 'lucide-react';

interface Props {
    user: UserProfile;
    children: ReactNode;
}

const roleConfig = {
    domain_owner: {
        label: 'Domain Owner',
        color: 'bg-purple-100 text-purple-800',
        nav: [
            { path: '/owner', label: 'Dashboard', icon: Home },
            { path: '/owner/evaluation', label: 'Evaluation', icon: Shield },
        ],
    },
    manager: {
        label: 'Manager',
        color: 'bg-blue-100 text-blue-800',
        nav: [
            { path: '/manager', label: 'Dashboard', icon: Home },
            { path: '/marketplace', label: 'Marketplace', icon: ShoppingBag },
            { path: '/cohorts/new', label: 'Build Cohort', icon: FlaskConical },
            { path: '/manager/evaluation', label: 'Evaluation', icon: Shield },
        ],
    },
    researcher: {
        label: 'Researcher',
        color: 'bg-green-100 text-green-800',
        nav: [
            { path: '/researcher', label: 'My Access', icon: Database },
        ],
    },
};

export default function Layout({ user, children }: Props) {
    const location = useLocation();
    const config = roleConfig[user.role];

    return (
        <div className="min-h-screen bg-gray-50">
            {/* Top bar */}
            <header className="bg-white border-b border-gray-200 sticky top-0 z-50">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex justify-between items-center h-16">
                        <div className="flex items-center space-x-4">
                            <Shield className="h-8 w-8 text-indigo-600" />
                            <span className="text-lg font-bold text-gray-900">
                                Clinical Trial Platform
                            </span>
                        </div>

                        <nav className="flex items-center space-x-1">
                            {config.nav.map(({ path, label, icon: Icon }) => (
                                <Link
                                    key={path}
                                    to={path}
                                    className={`flex items-center space-x-2 px-3 py-2 rounded-md text-sm font-medium transition-colors ${location.pathname === path
                                            ? 'bg-indigo-50 text-indigo-700'
                                            : 'text-gray-600 hover:bg-gray-100'
                                        }`}
                                >
                                    <Icon className="h-4 w-4" />
                                    <span>{label}</span>
                                </Link>
                            ))}
                        </nav>

                        <div className="flex items-center space-x-4">
                            <div className="text-right">
                                <p className="text-sm font-medium text-gray-900">{user.username}</p>
                                <div className="flex items-center space-x-2">
                                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${config.color}`}>
                                        {config.label}
                                    </span>
                                    <span className="text-xs text-gray-500">{user.organizationName}</span>
                                </div>
                            </div>
                            <button
                                onClick={() => keycloak.logout({ redirectUri: window.location.origin })}
                                className="p-2 text-gray-400 hover:text-gray-600 rounded-md hover:bg-gray-100"
                                title="Sign out"
                            >
                                <LogOut className="h-5 w-5" />
                            </button>
                        </div>
                    </div>
                </div>
            </header>

            {/* Content */}
            <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
                {children}
            </main>
        </div>
    );
}