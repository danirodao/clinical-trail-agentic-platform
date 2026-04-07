import React, { useState, useEffect, useRef } from 'react';
import { useStreamingQuery } from '../../hooks/useStreamingQuery';
import { TrialAccess, researcherApi } from '../../api/client';
import { Search, Loader2, Wrench, ShieldAlert, CheckCircle2, XCircle, MessageSquarePlus, Clock } from 'lucide-react';

interface QueryInterfaceProps {
    accessibleTrials: TrialAccess[];
}

interface ChatMessage {
    role: 'user' | 'agent';
    content: string;
}

export const QueryInterface: React.FC<QueryInterfaceProps> = ({ accessibleTrials }) => {
    // Session & History State
    const [sessionId, setSessionId] = useState<string>(() => crypto.randomUUID());
    const [chatHistory, setChatHistory] = useState<ChatMessage[]>([]);
    const [recentSessions, setRecentSessions] = useState<string[]>([]);

    // Existing Query State
    const [queryInput, setQueryInput] = useState('');
    const [selectedTrialIds, setSelectedTrialIds] = useState<string[]>([]);
    const messagesEndRef = useRef<HTMLDivElement>(null);

    const {
        sendQuery, isQuerying, statusMessage, answerText,
        activeTools, finalResponse, error, resetState
    } = useStreamingQuery();

    // Load recent sessions from local storage on mount
    useEffect(() => {
        const stored = localStorage.getItem('recent_sessions');
        if (stored) setRecentSessions(JSON.parse(stored));
    }, []);

    // Auto-scroll to bottom of messages
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [chatHistory, answerText, isQuerying]);

    const handleSend = () => {
        if (!queryInput.trim() || isQuerying) return;

        const currentQuery = queryInput;
        setQueryInput(''); // Clear input box instantly

        // Commit previous active response to history if it exists
        setChatHistory(prev => {
            let updated = [...prev];
            if (finalResponse && finalResponse.answer) {
                updated.push({ role: 'agent', content: finalResponse.answer });
            }
            updated.push({ role: 'user', content: currentQuery });
            return updated;
        });

        // Clear the streaming hook's state
        if (finalResponse || error) resetState();

        // Save session to history sidebar if it's new
        if (!recentSessions.includes(sessionId)) {
            const updatedSessions = [sessionId, ...recentSessions];
            setRecentSessions(updatedSessions);
            localStorage.setItem('recent_sessions', JSON.stringify(updatedSessions));
        }

        // Fire the API call
        sendQuery({
            query: currentQuery,
            trial_ids: selectedTrialIds.length > 0 ? selectedTrialIds : undefined,
            session_id: sessionId // Pass session ID!
        });
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    const toggleTrialSelection = (trialId: string) => {
        setSelectedTrialIds(prev =>
            prev.includes(trialId) ? prev.filter(id => id !== trialId) : [...prev, trialId]
        );
    };

    const handleNewChat = () => {
        if (isQuerying) return;
        setSessionId(crypto.randomUUID());
        setChatHistory([]);
        resetState();
    };

    const loadHistoricalSession = async (id: string) => {
        if (isQuerying) return;
        setSessionId(id);
        resetState();
        try {
            const res = await researcherApi.getHistory(id);
            setChatHistory(res.messages);
        } catch (e) {
            console.error("Failed to load history", e);
            setChatHistory([]);
        }
    };

    return (
        <div className="flex flex-col h-[calc(100vh-140px)] bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
            <div className="flex h-full">

                {/* ── Left Sidebar (History & Scope) ── */}
                <div className="w-1/4 border-r border-gray-200 flex flex-col bg-gray-50">

                    {/* Chat History Section */}
                    <div className="p-4 border-b border-gray-200">
                        <button
                            onClick={handleNewChat}
                            disabled={isQuerying}
                            className="w-full flex items-center justify-center space-x-2 bg-blue-600 text-white py-2 rounded-md hover:bg-blue-700 transition disabled:opacity-50"
                        >
                            <MessageSquarePlus className="w-4 h-4" />
                            <span>New Chat</span>
                        </button>

                        {recentSessions.length > 0 && (
                            <div className="mt-4 max-h-40 overflow-y-auto space-y-1">
                                <h3 className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-2">Recent Sessions</h3>
                                {recentSessions.map(id => (
                                    <button
                                        key={id}
                                        onClick={() => loadHistoricalSession(id)}
                                        className={`w-full text-left flex items-center px-2 py-1.5 text-xs rounded truncate ${id === sessionId ? 'bg-blue-100 text-blue-700 font-medium' : 'text-gray-600 hover:bg-gray-200'}`}
                                    >
                                        <Clock className="w-3 h-3 mr-2 shrink-0 opacity-70" />
                                        Session {id.split('-')[0]}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* Trial Scope Section */}
                    <div className="p-4 flex-1 overflow-y-auto">
                        <h3 className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-2">Query Scope</h3>
                        <p className="text-[10px] text-gray-400 mb-3">Select trials to narrow search.</p>

                        <div className="space-y-2">
                            {accessibleTrials.map(trial => (
                                <label key={trial.trial_id} className="flex items-start space-x-2 p-1.5 rounded hover:bg-gray-100 cursor-pointer border border-transparent hover:border-gray-200">
                                    <input
                                        type="checkbox"
                                        checked={selectedTrialIds.includes(trial.trial_id)}
                                        onChange={() => toggleTrialSelection(trial.trial_id)}
                                        className="mt-0.5 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                                    />
                                    <div className="flex-1 min-w-0">
                                        <div className="text-xs font-medium text-gray-900 truncate">{trial.nct_id}</div>
                                        <div className="mt-0.5 flex flex-wrap gap-1">
                                            <span className={`inline-flex px-1 py-0.5 rounded text-[9px] font-medium ${trial.access_level === 'individual' ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'}`}>
                                                {trial.access_level}
                                            </span>
                                        </div>
                                    </div>
                                </label>
                            ))}
                        </div>
                    </div>
                </div>

                {/* ── Main Chat Area ── */}
                <div className="flex-1 flex flex-col relative">

                    {/* Messages Area */}
                    <div className="flex-1 p-6 overflow-y-auto bg-gray-50/50">

                        {/* Empty State */}
                        {chatHistory.length === 0 && !isQuerying && !answerText && !error && !finalResponse && (
                            <div className="flex flex-col items-center justify-center h-full text-gray-500">
                                <Search className="w-12 h-12 mb-4 text-gray-300" />
                                <h2 className="text-lg font-medium text-gray-700 mb-2">Agentic Semantic Query</h2>
                                <p className="text-sm mb-6 text-center max-w-md">Ask natural language questions about your authorized clinical trial data.</p>
                                <div className="flex flex-wrap justify-center gap-2 max-w-2xl">
                                    {[
                                        "How many patients are enrolled across my trials?",
                                        "What are the most common adverse events?",
                                        "Tell me about the oncology trials."
                                    ].map(q => (
                                        <button key={q} onClick={() => setQueryInput(q)} className="px-3 py-1.5 text-sm bg-white border border-gray-200 rounded-full hover:border-blue-300 hover:bg-blue-50 text-gray-600 transition-colors">
                                            {q}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}

                        <div className="space-y-6 max-w-4xl mx-auto w-full">

                            {/* Render Historical Messages */}
                            {chatHistory.map((msg, idx) => (
                                <div key={idx} className="flex items-start space-x-3">
                                    <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white font-bold text-sm shrink-0 ${msg.role === 'user' ? 'bg-blue-600' : 'bg-indigo-600'}`}>
                                        {msg.role === 'user' ? 'You' : 'AI'}
                                    </div>
                                    <div className={`flex-1 p-4 rounded-lg shadow-sm text-sm whitespace-pre-wrap leading-relaxed border ${msg.role === 'user' ? 'bg-white border-gray-100 text-gray-800 rounded-tl-none' : 'bg-gray-50 border-gray-200 text-gray-700 rounded-tr-none'}`}>
                                        {msg.content}
                                    </div>
                                </div>
                            ))}

                            {/* Render Error Message */}
                            {error && (
                                <div className="p-4 bg-red-50 border border-red-200 rounded-lg flex items-start space-x-3">
                                    <ShieldAlert className="w-5 h-5 text-red-600 mt-0.5" />
                                    <div>
                                        <h4 className="text-sm font-medium text-red-800">Query Failed</h4>
                                        <p className="text-sm text-red-600 mt-1">{error}</p>
                                    </div>
                                </div>
                            )}

                            {/* Render Active Streaming/Finalized Message */}
                            {(isQuerying || answerText || finalResponse) && (
                                <div className="flex items-start space-x-3">
                                    <div className="w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center text-white font-bold text-sm shrink-0">
                                        AI
                                    </div>
                                    <div className="flex-1 min-w-0">

                                        {/* Status & Tool Calls Visualization */}
                                        <div className="mb-4 space-y-2">
                                            {isQuerying && statusMessage && (
                                                <div className="flex items-center text-xs text-gray-500 font-medium">
                                                    <Loader2 className="w-3 h-3 mr-2 animate-spin" />
                                                    {statusMessage}
                                                </div>
                                            )}

                                            {activeTools.map((tc, idx) => (
                                                <div key={idx} className="bg-white border border-gray-200 rounded-md p-2 shadow-sm text-sm">
                                                    <div className="flex items-center justify-between">
                                                        <div className="flex items-center text-indigo-700 font-medium">
                                                            <Wrench className="w-3 h-3 mr-2" />
                                                            {tc.tool}
                                                        </div>
                                                        <div className="flex items-center text-xs">
                                                            {tc.duration_ms ? <span className="text-gray-400 mr-2">{tc.duration_ms}ms</span> : <Loader2 className="w-3 h-3 text-gray-400 animate-spin mr-2" />}
                                                            {tc.status === 'success' && <CheckCircle2 className="w-4 h-4 text-green-500" />}
                                                            {tc.status === 'error' && <XCircle className="w-4 h-4 text-red-500" />}
                                                        </div>
                                                    </div>
                                                    {tc.summary && (
                                                        <div className={`mt-2 text-xs p-1.5 rounded ${tc.status === 'error' ? 'bg-red-50 text-red-700' : 'bg-gray-50 text-gray-600'}`}>
                                                            ↳ {tc.summary}
                                                        </div>
                                                    )}
                                                </div>
                                            ))}
                                        </div>

                                        {/* Streamed Answer Text */}
                                        {(answerText || finalResponse) && (
                                            <div className="bg-white p-5 rounded-lg rounded-tl-none shadow-sm border border-gray-100 text-gray-800 text-sm whitespace-pre-wrap leading-relaxed">
                                                {finalResponse ? finalResponse.answer : answerText}
                                                {isQuerying && answerText && <span className="inline-block w-1.5 h-4 ml-1 bg-indigo-500 animate-pulse align-middle" />}
                                            </div>
                                        )}

                                        {/* Rich Metadata Footer (Only shows on the actively finished response) */}
                                        {finalResponse && (
                                            <div className="mt-4 flex flex-wrap gap-3">
                                                <div className="flex items-center text-xs text-gray-500 bg-gray-100 px-2 py-1 rounded">
                                                    <span className="font-semibold mr-1">Access Level Applied:</span>
                                                    {finalResponse.access_level_applied}
                                                </div>
                                                <div className="flex items-center text-xs text-gray-500 bg-gray-100 px-2 py-1 rounded">
                                                    <span className="font-semibold mr-1">Model:</span>
                                                    {finalResponse.metadata.model_used}
                                                </div>
                                                {finalResponse.filters_applied.length > 0 && (
                                                    <div className="w-full mt-1">
                                                        <span className="text-[10px] font-semibold text-gray-500 block mb-1">Active Patient Filters:</span>
                                                        <div className="flex flex-wrap gap-1">
                                                            {finalResponse.filters_applied.map(f => (
                                                                <span key={f} className="text-[10px] bg-purple-50 border border-purple-100 text-purple-700 px-1.5 py-0.5 rounded">{f}</span>
                                                            ))}
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            )}

                            {/* Auto-scroll anchor */}
                            <div ref={messagesEndRef} />
                        </div>
                    </div>

                    {/* Input Area */}
                    <div className="p-4 bg-white border-t border-gray-200">
                        <div className="flex items-end space-x-2 max-w-4xl mx-auto w-full relative">
                            <textarea
                                value={queryInput}
                                onChange={(e) => setQueryInput(e.target.value)}
                                onKeyDown={handleKeyDown}
                                disabled={isQuerying}
                                placeholder="Ask a question about your clinical trials... (Shift+Enter for new line)"
                                className="flex-1 resize-none overflow-hidden rounded-lg border border-gray-300 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 p-3 pr-12 text-sm shadow-sm disabled:bg-gray-50 disabled:text-gray-500 min-h-[50px] max-h-[150px]"
                                rows={queryInput.split('\n').length > 1 ? Math.min(queryInput.split('\n').length, 5) : 1}
                            />
                            <button
                                onClick={handleSend}
                                disabled={!queryInput.trim() || isQuerying}
                                className="absolute right-2 bottom-2 p-2 bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                            >
                                <Search className="w-4 h-4" />
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
};