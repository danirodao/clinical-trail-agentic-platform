import { useEffect, useState } from 'react';
import { UserProfile } from '../keycloak';
import {
    evaluationApi,
    EvalStatusResponse,
    DatasetMetadata,
    EvalRunOptions,
} from '../api/client';
import {
    Activity,
    BarChart2,
    Database,
    ExternalLink,
    FileText,
    Play,
    RefreshCw,
    Shield,
    AlertTriangle,
    CheckCircle2,
    Clock,
    Layers,
    ArrowRight
} from 'lucide-react';
import StatusBadge from '../components/StatusBadge';

interface Props {
    user: UserProfile;
}

export default function EvaluationDashboard({ user: _user }: Props) {
    const [status, setStatus] = useState<EvalStatusResponse | null>(null);
    const [metadata, setMetadata] = useState<DatasetMetadata | null>(null);
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState<string | null>(null);
    const [error, setError] = useState('');
    const [message, setMessage] = useState('');

    // Sampling parameters
    const [samplingParams, setSamplingParams] = useState({
        sample_pct: 10,
        max_traces: 100,
        push_to_argilla: true
    });
    const [evalRunOptions, setEvalRunOptions] = useState<EvalRunOptions>({
        dataset_source: 'merged',
        max_cases: 50,
        argilla_sample_pct: 30,
    });

    useEffect(() => {
        loadData();
    }, []);

    async function loadData(showGlobalLoading: boolean = true) {
        if (showGlobalLoading) {
            setLoading(true);
        }
        try {
            const [s, m] = await Promise.all([
                evaluationApi.getLatestStatus(),
                evaluationApi.getDatasetMetadata()
            ]);
            setStatus(s);
            setMetadata(m);
        } catch (err: any) {
            setError(err.message || 'Failed to load evaluation data');
        } finally {
            if (showGlobalLoading) {
                setLoading(false);
            }
        }
    }

    async function handleAction(type: string, actionFn: () => Promise<any>) {
        setActionLoading(type);
        setError('');
        setMessage('');
        try {
            const res = await actionFn();
            setMessage(
                res?.message || (res?.status === 'ok' ? 'Action completed successfully' : 'Success')
            );
        } catch (err: any) {
            setError(err.message || 'Action failed');
        } finally {
            setActionLoading(null);
            // Refresh dashboard data without triggering the full-page loading spinner.
            void loadData(false);
        }
    }

    if (loading) {
        return (
            <div className="flex items-center justify-center py-20">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600" />
            </div>
        );
    }

    return (
        <div className="space-y-8 animate-in fade-in duration-500">
            {/* Header */}
            <div className="flex justify-between items-start">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
                        <Activity className="h-6 w-6 text-indigo-600" />
                        Evaluation Control Center
                    </h1>
                    <p className="mt-1 text-sm text-gray-500">
                        Monitor system quality, manage golden datasets, and sync human-in-the-loop corrections.
                    </p>
                </div>
                <div className="flex gap-3">
                    <button
                        onClick={() => handleAction('sample', () =>
                            evaluationApi.runEvaluation('sample_dataset.json', undefined, true, evalRunOptions)
                        )}
                        disabled={!!actionLoading}
                        className="flex items-center gap-2 px-4 py-2 bg-white border border-gray-200 text-gray-700 rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-all font-medium shadow-sm"
                    >
                        {actionLoading === 'sample' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                        Run Sample Test
                    </button>
                    <button
                        onClick={() => handleAction('run', () =>
                            evaluationApi.runEvaluation('golden_dataset.json', undefined, true, evalRunOptions)
                        )}
                        disabled={!!actionLoading}
                        className="flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-all font-medium shadow-sm"
                    >
                        {actionLoading === 'run' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                        Run Full Evaluation
                    </button>
                    <button
                        onClick={() => handleAction('sync', () => evaluationApi.importReviewed())}
                        disabled={!!actionLoading}
                        className="flex items-center gap-2 px-4 py-2 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-all font-medium shadow-sm"
                    >
                        {actionLoading === 'sync' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                        Flywheel Sync (Argilla)
                    </button>
                </div>
            </div>

            {/* Notifications */}
            {error && (
                <div className="p-4 bg-red-50 border border-red-200 rounded-xl flex items-center gap-3 text-red-700">
                    <AlertTriangle className="h-5 w-5 flex-shrink-0" />
                    <span>{error}</span>
                </div>
            )}
            {message && (
                <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-xl flex items-center gap-3 text-emerald-700">
                    <CheckCircle2 className="h-5 w-5 flex-shrink-0" />
                    <span>{message}</span>
                </div>
            )}

            <div className="bg-white p-4 rounded-xl border border-gray-100">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-2">
                            Evaluation Source
                        </label>
                        <select
                            value={evalRunOptions.dataset_source}
                            onChange={(e) =>
                                setEvalRunOptions({
                                    ...evalRunOptions,
                                    dataset_source: e.target.value as EvalRunOptions['dataset_source'],
                                })
                            }
                            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                        >
                            <option value="merged">Merged (static + Argilla)</option>
                            <option value="static">Static dataset only</option>
                            <option value="argilla">Argilla reviewed only</option>
                        </select>
                    </div>
                    <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-2">
                            Max Cases (cost cap)
                        </label>
                        <input
                            type="number"
                            min={0}
                            max={5000}
                            value={evalRunOptions.max_cases ?? '0'}
                            onChange={(e) =>
                                setEvalRunOptions({
                                    ...evalRunOptions,
                                    max_cases: e.target.value ? parseInt(e.target.value, 10) : undefined,
                                })
                            }
                            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                        />
                    </div>
                    <div>
                        <label className="block text-xs font-bold text-gray-500 uppercase mb-2">
                            Argilla Sample %
                        </label>
                        <input
                            type="number"
                            min={1}
                            max={100}
                            value={evalRunOptions.argilla_sample_pct ?? 100}
                            onChange={(e) =>
                                setEvalRunOptions({
                                    ...evalRunOptions,
                                    argilla_sample_pct: parseFloat(e.target.value || '100'),
                                })
                            }
                            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                        />
                    </div>
                </div>
            </div>

            {/* Summary Stats */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                <div className="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 flex flex-col justify-between group hover:shadow-md transition-shadow">
                    <div className="flex justify-between items-start mb-4">
                        <div className="p-2 bg-indigo-50 rounded-lg group-hover:bg-indigo-100 transition-colors">
                            <BarChart2 className="h-5 w-5 text-indigo-600" />
                        </div>
                        <span className="text-xs font-medium text-indigo-600 bg-indigo-50 px-2 py-1 rounded-full">v{metadata?.version}</span>
                    </div>
                    <div>
                        <p className="text-sm font-medium text-gray-500 uppercase tracking-wider">Pass Rate</p>
                        <p className="text-3xl font-bold text-gray-900">{status?.pass_rate ? (status.pass_rate * 100).toFixed(1) : 0}%</p>
                    </div>
                </div>

                <div className="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 flex flex-col justify-between group hover:shadow-md transition-shadow">
                    <div className="flex justify-between items-start mb-4">
                        <div className="p-2 bg-amber-50 rounded-lg group-hover:bg-amber-100 transition-colors">
                            <Layers className="h-5 w-5 text-amber-600" />
                        </div>
                    </div>
                    <div>
                        <p className="text-sm font-medium text-gray-500 uppercase tracking-wider">Tested Cases</p>
                        <p className="text-3xl font-bold text-gray-900">{status?.total_cases || 0}</p>
                    </div>
                </div>

                <div className="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 flex flex-col justify-between group hover:shadow-md transition-shadow">
                    <div className="flex justify-between items-start mb-4">
                        <div className="p-2 bg-emerald-50 rounded-lg group-hover:bg-emerald-100 transition-colors">
                            <Database className="h-5 w-5 text-emerald-600" />
                        </div>
                    </div>
                    <div>
                        <p className="text-sm font-medium text-gray-500 uppercase tracking-wider">Golden Dataset</p>
                        <p className="text-3xl font-bold text-gray-900">{metadata?.total_cases || 0} cases</p>
                    </div>
                </div>

                <div className="bg-white p-6 rounded-2xl shadow-sm border border-gray-100 flex flex-col justify-between group hover:shadow-md transition-shadow">
                    <div className="flex justify-between items-start mb-4">
                        <div className="p-2 bg-blue-50 rounded-lg group-hover:bg-blue-100 transition-colors">
                            <Clock className="h-5 w-5 text-blue-600" />
                        </div>
                    </div>
                    <div>
                        <p className="text-sm font-medium text-gray-500 uppercase tracking-wider">Last Run</p>
                        <p className="text-sm font-medium text-gray-900 mt-2">{status?.latest_run ? new Date(status.latest_run.replace('eval-', '').replace('.json', '')).toLocaleString() : 'Never'}</p>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                {/* Aggregate Scores Table */}
                <div className="lg:col-span-2 space-y-8">
                    {/* Metrics Breakdown */}
                    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
                        <div className="px-6 py-4 border-b border-gray-100 bg-gray-50/50 flex justify-between items-center">
                            <h2 className="font-bold text-gray-900">Metric Distribution</h2>
                            <BarChart2 className="h-4 w-4 text-gray-400" />
                        </div>
                        <div className="p-6">
                            {status?.aggregate_scores ? (
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-12 gap-y-6">
                                    {Object.entries(status.aggregate_scores).map(([metric, score]) => {
                                        const isInverted = ['toxicity', 'bias', 'pii_leakage'].includes(metric);
                                        // For inverted metrics, lower score is better (greener). For normal, higher is better.
                                        const isGood = isInverted ? score <= 0.3 : score >= 0.7;
                                        const isOk = isInverted ? score <= 0.6 : score !== null; // Just simplified OK threshold

                                        const textColor = isGood ? 'text-emerald-600' : 'text-amber-600';
                                        
                                        let bgColor = 'bg-red-500';
                                        if (isInverted) {
                                            if (score <= 0.3) bgColor = 'bg-emerald-500';
                                            else if (score <= 0.6) bgColor = 'bg-amber-500';
                                        } else {
                                            if (score >= 0.8) bgColor = 'bg-emerald-500';
                                            else if (score >= 0.6) bgColor = 'bg-amber-500';
                                        }

                                        // For visual representation of inverted metrics, maybe we fill it reversed or just show it small but green?
                                        // If toxicity is 0%, a 0px green bar is hard to see. Let's invert the width for inverted metrics,
                                        // OR just let it be small. Usually a progress bar for toxicity of 100% being full red makes sense. 
                                        // Wait, the user said "shouldn't it be green when they are 0%". Just fixing the color is enough.
                                        // Actually, let's keep the width exactly representing the value so 0% = 0 width, but if it has any width it's correctly colored.
                                        // Or better, set a minimum width so the color is consistently visible.
                                        const barWidth = Math.max(score * 100, 2);

                                        return (
                                            <div key={metric} className="group">
                                                <div className="flex justify-between items-center mb-2">
                                                    <span className="text-sm font-semibold text-gray-600 capitalize">{metric.replace(/_/g, ' ')}</span>
                                                    <span className={`text-sm font-bold ${textColor}`}>{(score * 100).toFixed(0)}%</span>
                                                </div>
                                                <div className="w-full bg-gray-100 rounded-full h-1.5 overflow-hidden">
                                                    <div
                                                        className={`h-full rounded-full transition-all duration-1000 group-hover:opacity-80 ${bgColor}`}
                                                        style={{ width: `${barWidth}%` }}
                                                    />
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            ) : (
                                <div className="text-center py-10 text-gray-500 italic">No metrics data available. Run an evaluation to generate results.</div>
                            )}
                        </div>
                    </div>

                    {/* Recently Failed Cases (Mocked/Static for now as API returns IDs only) */}
                    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
                        <div className="px-6 py-4 border-b border-gray-100 bg-gray-50/50 flex justify-between items-center">
                            <div className="flex items-center gap-2">
                                <h2 className="font-bold text-gray-900">Recently Failed Cases</h2>
                                <span className="text-xs bg-red-100 text-red-600 px-2 py-0.5 rounded-full font-bold">
                                    {status?.failed_cases || 0} Issues
                                </span>
                            </div>
                            <AlertTriangle className="h-4 w-4 text-amber-500" />
                        </div>
                        <div className="overflow-x-auto">
                            <table className="min-w-full divide-y divide-gray-200">
                                <thead className="bg-gray-50">
                                    <tr>
                                        <th className="px-6 py-3 text-left text-xs font-bold text-gray-500 uppercase tracking-wider">Case ID</th>
                                        <th className="px-6 py-3 text-left text-xs font-bold text-gray-500 uppercase tracking-wider">Quality Score</th>
                                        <th className="px-6 py-3 text-right text-xs font-bold text-gray-500 uppercase tracking-wider">Action</th>
                                    </tr>
                                </thead>
                                <tbody className="bg-white divide-y divide-gray-100">
                                    {/* Backend only returns IDs currently, so we show the IDs and link to Argilla/Phoenix for details */}
                                    {status?.failed_cases && status.failed_cases > 0 ? (
                                        <tr className="hover:bg-gray-50 transition-colors">
                                            <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900 italic">
                                                Detail available in Phoenix / Argilla boards
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap">
                                                <StatusBadge status="sensitive" />
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap text-right text-sm">
                                                <a href="http://localhost:6900" className="text-indigo-600 hover:text-indigo-900 font-bold flex items-center justify-end gap-1">
                                                    Review <ArrowRight className="h-3 w-3" />
                                                </a>
                                            </td>
                                        </tr>
                                    ) : (
                                        <tr>
                                            <td colSpan={3} className="px-6 py-10 text-center text-sm text-gray-500">
                                                No failed cases in the last run. Your system is performing within thresholds!
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    {/* Sampling Configuration */}
                    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
                        <div className="px-6 py-4 border-b border-gray-100 bg-gray-50/50 flex justify-between items-center">
                            <h2 className="font-bold text-gray-900">Production Trace Sampling (Phoenix)</h2>
                            <Database className="h-4 w-4 text-gray-400" />
                        </div>
                        <div className="p-6">
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-6">
                                <div>
                                    <label className="block text-xs font-bold text-gray-500 uppercase mb-2">Sample Percentage</label>
                                    <div className="flex items-center gap-4">
                                        <input
                                            type="range" min="1" max="100"
                                            value={samplingParams.sample_pct}
                                            onChange={(e) => setSamplingParams({ ...samplingParams, sample_pct: parseInt(e.target.value) })}
                                            className="flex-1 accent-indigo-600"
                                        />
                                        <span className="text-sm font-bold w-12 text-center">{samplingParams.sample_pct}%</span>
                                    </div>
                                </div>
                                <div>
                                    <label className="block text-xs font-bold text-gray-500 uppercase mb-2">Max Traces</label>
                                    <input
                                        type="number"
                                        value={samplingParams.max_traces}
                                        onChange={(e) => setSamplingParams({ ...samplingParams, max_traces: parseInt(e.target.value) })}
                                        className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:ring-2 focus:ring-indigo-500 outline-none"
                                    />
                                </div>
                                <div className="flex items-end">
                                    <label className="flex items-center gap-3 cursor-pointer p-2 hover:bg-gray-50 rounded-lg transition-colors">
                                        <input
                                            type="checkbox"
                                            checked={samplingParams.push_to_argilla}
                                            onChange={(e) => setSamplingParams({ ...samplingParams, push_to_argilla: e.target.checked })}
                                            className="h-4 w-4 rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                                        />
                                        <span className="text-sm font-medium text-gray-700">Push to Argilla for review</span>
                                    </label>
                                </div>
                            </div>
                            <button
                                onClick={() => handleAction('build', () => evaluationApi.buildDataset(samplingParams))}
                                disabled={!!actionLoading}
                                className="w-full py-2 bg-white border border-gray-200 text-gray-700 rounded-lg hover:bg-gray-50 shadow-sm font-medium flex items-center justify-center gap-2 transition-all"
                            >
                                {actionLoading === 'build' ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Database className="h-4 w-4" />}
                                Start Production Sampling Run
                            </button>
                        </div>
                    </div>
                </div>

                {/* Sidebar: External Links & Metadata */}
                <div className="space-y-8">
                    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden">
                        <div className="px-6 py-4 border-b border-gray-100 bg-gray-50/50">
                            <h2 className="font-bold text-gray-900">External Boards</h2>
                        </div>
                        <div className="p-4 space-y-2">
                            <a
                                href="http://localhost:3000" target="_blank" rel="noreferrer"
                                className="flex items-center justify-between p-3 rounded-xl hover:bg-indigo-50 text-gray-700 hover:text-indigo-700 transition-all group"
                            >
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-gray-50 rounded-lg group-hover:bg-white transition-colors">
                                        <BarChart2 className="h-4 w-4" />
                                    </div>
                                    <span className="text-sm font-medium">Grafana Dashboard</span>
                                </div>
                                <ExternalLink className="h-4 w-4 opacity-0 group-hover:opacity-100 transition-opacity" />
                            </a>
                            <a
                                href="http://localhost:6900" target="_blank" rel="noreferrer"
                                className="flex items-center justify-between p-3 rounded-xl hover:bg-emerald-50 text-gray-700 hover:text-emerald-700 transition-all group"
                            >
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-gray-50 rounded-lg group-hover:bg-white transition-colors">
                                        <Shield className="h-4 w-4" />
                                    </div>
                                    <span className="text-sm font-medium">Argilla (Review Failures)</span>
                                </div>
                                <ExternalLink className="h-4 w-4 opacity-0 group-hover:opacity-100 transition-opacity" />
                            </a>
                            <a
                                href="http://localhost:6006" target="_blank" rel="noreferrer"
                                className="flex items-center justify-between p-3 rounded-xl hover:bg-amber-50 text-gray-700 hover:text-amber-700 transition-all group"
                            >
                                <div className="flex items-center gap-3">
                                    <div className="p-2 bg-gray-50 rounded-lg group-hover:bg-white transition-colors">
                                        <Database className="h-4 w-4" />
                                    </div>
                                    <span className="text-sm font-medium">Phoenix (Production Traces)</span>
                                </div>
                                <ExternalLink className="h-4 w-4 opacity-0 group-hover:opacity-100 transition-opacity" />
                            </a>
                        </div>
                    </div>

                    <div className="bg-indigo-900 rounded-2xl p-6 text-white shadow-lg overflow-hidden relative">
                        <div className="relative z-10">
                            <h3 className="font-bold mb-2 flex items-center gap-2">
                                <FileText className="h-4 w-4" />
                                Golden Dataset Info
                            </h3>
                            <div className="space-y-3 mt-4">
                                <div className="flex justify-between text-sm opacity-80">
                                    <span>Version</span>
                                    <span className="font-mono">v{metadata?.version}</span>
                                </div>
                                <div className="flex justify-between text-sm opacity-80 border-t border-white/10 pt-2">
                                    <span>Agent Layer</span>
                                    <span className="font-bold">{metadata?.agent_cases} cases</span>
                                </div>
                                <div className="flex justify-between text-sm opacity-80 border-t border-white/10 pt-2">
                                    <span>MCP Layer</span>
                                    <span className="font-bold">{metadata?.mcp_cases} cases</span>
                                </div>
                                <div className="mt-4 p-3 bg-white/10 rounded-xl rounded-tl-none text-xs">
                                    <p className="font-semibold mb-1">Pass Thresholds</p>
                                    {metadata?.thresholds && Object.entries(metadata.thresholds).map(([k, v]) => (
                                        <span key={k} className="mr-2 opacity-70 capitalize">{k.replace('_', ' ')}: {v}</span>
                                    ))}
                                </div>
                            </div>
                        </div>
                        <Shield className="absolute -bottom-10 -right-10 h-40 w-40 text-white/5" />
                    </div>
                </div>
            </div>
        </div>
    );
}
