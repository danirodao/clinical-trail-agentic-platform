import { useState, useCallback, useRef } from 'react';
import { researcherApi, QueryRequest, QueryResponse, ToolCallRecord } from '../api/client';

interface ActiveToolCall {
    tool: string;
    args: Record<string, any>;
    summary?: string;
    duration_ms?: number;
    status?: string;
}

export function useStreamingQuery() {
    const [isQuerying, setIsQuerying] = useState(false);
    const [statusMessage, setStatusMessage] = useState<string>('');
    const [answerText, setAnswerText] = useState<string>('');
    const [activeTools, setActiveTools] = useState<ActiveToolCall[]>([]);
    const [finalResponse, setFinalResponse] = useState<QueryResponse | null>(null);
    const [error, setError] = useState<string | null>(null);

    const abortControllerRef = useRef<AbortController | null>(null);

    const resetState = () => {
        setStatusMessage('');
        setAnswerText('');
        setActiveTools([]);
        setFinalResponse(null);
        setError(null);
    };

    const cancelQuery = useCallback(() => {
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
            setIsQuerying(false);
            setStatusMessage('Query cancelled.');
        }
    }, []);

    const sendQuery = useCallback(async (request: QueryRequest) => {
        resetState();
        setIsQuerying(true);
        abortControllerRef.current = new AbortController();

        try {
            const reader = await researcherApi.queryStream(request);
            const decoder = new TextDecoder('utf-8');
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();

                if (done) break;

                buffer += decoder.decode(value, { stream: true });

                // NDJSON parsing: split by newline
                const lines = buffer.split('\n');
                buffer = lines.pop() || ''; // Keep the incomplete last line in the buffer

                for (const line of lines) {
                    if (!line.trim()) continue;

                    try {
                        const event = JSON.parse(line);

                        switch (event.event) {
                            case 'status':
                                setStatusMessage(event.data.message);
                                break;

                            case 'tool_call':
                                setActiveTools(prev => [...prev, {
                                    tool: event.data.tool,
                                    args: event.data.args
                                }]);
                                break;

                            case 'tool_result':
                                // Update the matching tool call in the array
                                setActiveTools(prev => {
                                    const newTools = [...prev];
                                    // Find the last tool call that matches the name and doesn't have a summary yet
                                    const idx = [...newTools].reverse().findIndex(
                                        t => t.tool === event.data.tool && !t.summary
                                    );
                                    if (idx !== -1) {
                                        const realIdx = newTools.length - 1 - idx;
                                        newTools[realIdx] = {
                                            ...newTools[realIdx],
                                            summary: event.data.summary,
                                            duration_ms: event.data.duration_ms,
                                            status: event.data.status
                                        };
                                    }
                                    return newTools;
                                });
                                break;

                            case 'answer_token':
                                setAnswerText(prev => prev + event.data.token);
                                break;

                            case 'complete':
                                setFinalResponse(event.data);
                                setIsQuerying(false);
                                break;

                            case 'error':
                                setError(event.data.message);
                                setIsQuerying(false);
                                break;
                        }
                    } catch (e) {
                        console.error('Failed to parse NDJSON line:', line, e);
                    }
                }
            }
        } catch (err: any) {
            setError(err.message || 'Network request failed');
            setIsQuerying(false);
        }
    }, []);

    return {
        sendQuery,
        cancelQuery,
        isQuerying,
        statusMessage,
        answerText,
        activeTools,
        finalResponse,
        error,
        resetState
    };
}