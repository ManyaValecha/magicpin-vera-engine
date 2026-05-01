import React, { useState, useEffect, useCallback } from 'react';
import {
    Activity,
    Database,
    Send,
    RefreshCcw,
    CheckCircle2,
    XCircle,
    Clock,
    MessageSquare,
    ChevronRight,
    Code2,
    Terminal,
    Layers,
    ArrowRight,
    Zap
} from 'lucide-react';

const API_BASE = "";

const DEMO_DATA = {
    merchant: {
        merchant_id: "m_001_drmeera_dentist_delhi",
        category_slug: "dentists",
        identity: {
            name: "Dr. Meera's Dental Clinic",
            city: "Delhi",
            locality: "Lajpat Nagar",
            owner_first_name: "Meera"
        },
        performance: {
            views: 2410,
            calls: 18,
            ctr: 0.021
        }
    },
    trigger: {
        id: "t_001_dentist_stale_post",
        kind: "performance_alert",
        urgency: "high",
        payload: {
            type: "stale_posts",
            days_stale: 22
        }
    },
    customer: {
        customer_id: "c_001_priya",
        merchant_id: "m_001_drmeera_dentist_delhi",
        identity: {
            name: "Priya",
            language_pref: "hi-en mix"
        },
        relationship: {
            visits_total: 4,
            last_visit: "2026-05-12"
        }
    },
    category: {
        slug: "dentists",
        display_name: "Dentists",
        voice: {
            tone: "peer_clinical",
            salutation_examples: ["Dr. {first_name}", "Doc"]
        }
    }
};

const StatCard = ({ title, value, icon: Icon, subValue }) => (
    <div className="bg-[#151518] p-5 rounded-lg border border-[#2b2b2f] item-hover transition-colors">
        <div className="flex items-center gap-3 mb-3">
            <div className="p-2 bg-[#2b2b2f] rounded-md text-[#8a8b91]">
                <Icon size={16} />
            </div>
            <p className="text-[11px] font-bold uppercase tracking-wider text-[#8a8b91]">{title}</p>
        </div>
        <div>
            <h3 className="text-xl font-bold text-white tracking-tight">{value}</h3>
            {subValue && <p className="text-[10px] text-[#8a8b91] mt-1 font-medium">{subValue}</p>}
        </div>
    </div>
);

const NavItem = ({ icon: Icon, label, active, onClick }) => (
    <button
        onClick={onClick}
        className={`w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-all ${active
            ? 'bg-[#2b2b2f] text-white shadow-sm'
            : 'text-[#8a8b91] hover:text-white hover:bg-[#1c1c1f]'
            }`}
    >
        <Icon size={16} />
        {label}
    </button>
);

const App = () => {
    const [activeTab, setActiveTab] = useState('merchant');
    const [jsonInput, setJsonInput] = useState(JSON.stringify(DEMO_DATA.merchant, null, 2));
    const [logs, setLogs] = useState([]);
    const [stats, setStats] = useState({
        status: 'checking',
        contexts: { category: 0, merchant: 0, customer: 0, trigger: 0 },
        latency: '0ms',
        lastTick: 'Never'
    });
    const [actions, setActions] = useState([]);
    const [replyInput, setReplyInput] = useState('');
    const [chat, setChat] = useState([]);
    const [loading, setLoading] = useState({});

    const addLog = useCallback((message, type = 'info') => {
        setLogs(prev => [{
            id: Date.now(),
            time: new Date().toLocaleTimeString(),
            message,
            type
        }, ...prev].slice(0, 50));
    }, []);

    const fetchStats = useCallback(async () => {
        try {
            const resp = await fetch(`${API_BASE}/v1/healthz`);
            const data = await resp.json();
            setStats(prev => ({
                ...prev,
                status: data.status === 'ok' ? 'Online' : 'Warning',
                contexts: data.contexts_loaded
            }));
        } catch (e) {
            setStats(prev => ({ ...prev, status: 'Offline' }));
        }
    }, []);

    useEffect(() => {
        fetchStats();
        const inv = setInterval(fetchStats, 10000);
        return () => clearInterval(inv);
    }, [fetchStats]);

    const handleContextSubmit = async () => {
        setLoading(prev => ({ ...prev, context: true }));
        try {
            // Sanitize smart quotes common on Mac copy-paste
            const sanitized = jsonInput
                .replace(/[\u201C\u201D]/g, '"')
                .replace(/[\u2018\u2019]/g, "'");

            const payload = JSON.parse(sanitized);
            const res = await fetch(`${API_BASE}/v1/context`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scope: activeTab,
                    context_id: payload.merchant_id || payload.customer_id || payload.slug || payload.id,
                    version: 1,
                    payload,
                    delivered_at: new Date().toISOString()
                })
            });
            const data = await res.json();
            if (res.ok) {
                addLog(`Context for ${activeTab} loaded`, 'success');
                fetchStats();
            } else {
                addLog(`Update failed: ${data.reason || data.detail || 'Unknown error'}`, 'error');
            }
        } catch (e) {
            addLog(`JSON Error: ${e.message}`, 'error');
        }
        setLoading(prev => ({ ...prev, context: false }));
    };

    const handleTick = async () => {
        setLoading(prev => ({ ...prev, tick: true }));
        const start = Date.now();
        try {
            const res = await fetch(`${API_BASE}/v1/tick`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    now: new Date().toISOString(),
                    available_triggers: [DEMO_DATA.trigger.id]
                })
            });
            const data = await res.json();
            setActions(data.actions || []);
            setStats(prev => ({
                ...prev,
                latency: `${Date.now() - start}ms`,
                lastTick: new Date().toLocaleTimeString()
            }));
            addLog(`Executed tick. Found ${data.actions?.length || 0} actions.`, 'success');
        } catch (e) {
            addLog(`Tick execution failed`, 'error');
        }
        setLoading(prev => ({ ...prev, tick: false }));
    };

    const handleReply = async () => {
        if (!replyInput.trim()) return;
        const msg = replyInput;
        setReplyInput('');
        setChat(prev => [...prev, { role: 'merchant', text: msg }]);

        setLoading(prev => ({ ...prev, reply: true }));
        try {
            // Dynamically get merchant_id from the current context being edited
            let merchantId = "m_001_drmeera_dentist_delhi"; // Fallback
            try {
                const currentData = JSON.parse(jsonInput);
                merchantId = currentData.merchant_id || currentData.id || activeTab === 'merchant' ? currentData.merchant_id : "m_001_drmeera_dentist_delhi";
            } catch (e) { }

            const res = await fetch(`${API_BASE}/v1/reply`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    conversation_id: `conv_${merchantId}`,
                    merchant_id: merchantId,
                    from_role: "merchant",
                    message: msg,
                    received_at: new Date().toISOString(),
                    turn_number: chat.length + 1
                })
            });
            const data = await res.json();
            setChat(prev => [...prev, { role: 'vera', text: data.body || `[Action: ${data.action}]` }]);
            addLog(`Reply processed: ${data.action}`, 'info');
        } catch (e) {
            addLog(`Reply failed`, 'error');
        }
        setLoading(prev => ({ ...prev, reply: false }));
    };

    return (
        <div className="flex h-screen bg-[#0c0c0e] text-[#eeeeee] font-sans overflow-hidden">
            {/* Nav Sidebar */}
            <aside className="w-64 border-r border-[#2b2b2f] flex flex-col bg-[#0c0c0e]">
                <div className="p-6 flex items-center gap-3">
                    <div className="w-6 h-6 linear-gradient rounded flex items-center justify-center text-white">
                        <Zap size={14} className="fill-current" />
                    </div>
                    <h1 className="text-sm font-bold tracking-tight">Vera Growth</h1>
                </div>

                <nav className="flex-1 px-3 space-y-1">
                    <p className="px-3 text-[10px] font-bold text-[#4a4b50] uppercase tracking-widest mb-2 mt-4">Context</p>
                    <NavItem icon={Database} label="Merchant" active={activeTab === 'merchant'} onClick={() => { setActiveTab('merchant'); setJsonInput(JSON.stringify(DEMO_DATA.merchant, null, 2)) }} />
                    <NavItem icon={Zap} label="Trigger" active={activeTab === 'trigger'} onClick={() => { setActiveTab('trigger'); setJsonInput(JSON.stringify(DEMO_DATA.trigger, null, 2)) }} />
                    <NavItem icon={Layers} label="Customer" active={activeTab === 'customer'} onClick={() => { setActiveTab('customer'); setJsonInput(JSON.stringify(DEMO_DATA.customer, null, 2)) }} />
                    <NavItem icon={Activity} label="Category" active={activeTab === 'category'} onClick={() => { setActiveTab('category'); setJsonInput(JSON.stringify(DEMO_DATA.category, null, 2)) }} />

                    <p className="px-3 text-[10px] font-bold text-[#4a4b50] uppercase tracking-widest mb-2 mt-8">Monitoring</p>
                    <button className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-[#8a8b91] hover:bg-[#1c1c1f]">
                        <Terminal size={16} />
                        API Logs
                    </button>
                    <button className="w-full flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium text-[#8a8b91] hover:bg-[#1c1c1f]">
                        <Clock size={16} />
                        History
                    </button>
                </nav>

                <div className="p-4 mt-auto border-t border-[#2b2b2f]">
                    <div className="flex items-center justify-between p-2 rounded-md bg-[#151518] border border-[#2b2b2f]">
                        <div className="flex items-center gap-2">
                            <span className={`w-1.5 h-1.5 rounded-full ${stats.status === 'Online' ? 'bg-[#5147e7] shadow-[0_0_8px_#5147e7]' : 'bg-red-500'}`} />
                            <span className="text-[10px] font-bold text-[#8a8b91]">{stats.status}</span>
                        </div>
                        <span className="text-[10px] font-mono text-[#4a4b50]">{stats.latency}</span>
                    </div>
                </div>
            </aside>

            {/* Main Content Area */}
            <main className="flex-1 flex flex-col min-w-0">
                {/* Topbar */}
                <header className="h-14 border-b border-[#2b2b2f] flex items-center px-8 justify-between bg-[#0c0c0e]/50 backdrop-blur-xl sticky top-0 z-50">
                    <div className="flex items-center gap-2 text-sm">
                        <span className="text-[#8a8b91]">Dashboard</span>
                        <ChevronRight size={14} className="text-[#4a4b50]" />
                        <span className="font-medium text-white">{activeTab.charAt(0).toUpperCase() + activeTab.slice(1)}</span>
                    </div>
                    <div className="flex items-center gap-4">
                        <button onClick={fetchStats} className="p-1.5 rounded hover:bg-[#1c1c1f] text-[#8a8b91] transition-colors">
                            <RefreshCcw size={16} />
                        </button>
                    </div>
                </header>

                <div className="flex-1 overflow-y-auto overflow-x-hidden p-8 space-y-8">
                    {/* Header stats */}
                    <div className="grid grid-cols-4 gap-4">
                        <StatCard title="Vitals" value={stats.status} icon={Activity} subValue={`Latency ${stats.latency}`} />
                        <StatCard title="Context" value={Object.values(stats.contexts).reduce((a, b) => a + b, 0)} icon={Database} subValue="Items in cache" />
                        <StatCard title="Last Run" value={stats.lastTick} icon={Clock} subValue="Execution timestamp" />
                        <StatCard title="Throughput" value="1.2s" icon={Zap} subValue="P99 latency" />
                    </div>

                    <div className="grid grid-cols-12 gap-8 items-start">
                        {/* Editor Section */}
                        <div className="col-span-12 lg:col-span-7 space-y-6">
                            <div className="bg-[#151518] rounded-xl border border-[#2b2b2f] overflow-hidden flex flex-col">
                                <div className="px-4 py-3 border-b border-[#2b2b2f] bg-[#1c1c1f] flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <Code2 size={14} className="text-[#8a8b91]" />
                                        <span className="text-[11px] font-bold text-white uppercase tracking-wider">State Hydrator</span>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <button onClick={() => setJsonInput(JSON.stringify(DEMO_DATA[activeTab], null, 2))} className="text-[10px] font-bold text-[#8a8b91] hover:text-white px-2 py-1 rounded transition-colors uppercase tracking-tight">Revert</button>
                                        <button onClick={handleContextSubmit} disabled={loading.context} className="bg-white text-black text-[10px] font-bold px-3 py-1 rounded hover:opacity-90 transition-opacity uppercase tracking-tight">Update Context</button>
                                    </div>
                                </div>
                                <div className="relative">
                                    <textarea
                                        value={jsonInput}
                                        onChange={(e) => setJsonInput(e.target.value)}
                                        className="w-full h-[400px] bg-[#0c0c0e] text-[#d1d1d1] font-mono text-[13px] p-6 resize-none outline-none focus:ring-0"
                                        spellCheck="false"
                                    />
                                    {loading.context && (
                                        <div className="absolute inset-0 bg-black/20 backdrop-blur-[1px] flex items-center justify-center">
                                            <RefreshCcw size={20} className="animate-spin text-white" />
                                        </div>
                                    )}
                                </div>
                            </div>

                            <div className="bg-[#151518] rounded-xl border border-[#2b2b2f]">
                                <div className="px-4 py-3 border-b border-[#2b2b2f] bg-[#1c1c1f] flex items-center gap-2">
                                    <Terminal size={14} className="text-[#8a8b91]" />
                                    <span className="text-[11px] font-bold text-white uppercase tracking-wider">Output stream</span>
                                </div>
                                <div className="p-4 space-y-1 h-48 overflow-y-auto custom-scrollbar">
                                    {logs.map(log => (
                                        <div key={log.id} className="flex gap-4 group">
                                            <span className="text-[#4a4b50] font-mono text-[11px] shrink-0">{log.time}</span>
                                            <span className={`text-[11px] flex-1 ${log.type === 'error' ? 'text-red-400' : log.type === 'success' ? 'text-[#5147e7]' : 'text-[#8a8b91]'
                                                }`}>
                                                {log.message}
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        </div>

                        {/* Recommendation Side */}
                        <div className="col-span-12 lg:col-span-5 space-y-6">
                            <div className="bg-[#151518] rounded-xl border border-[#2b2b2f] p-6 shadow-2xl">
                                <div className="flex items-center justify-between mb-8">
                                    <div>
                                        <h2 className="text-sm font-bold text-white mb-1">Growth Decision</h2>
                                        <p className="text-[11px] text-[#8a8b91]">Vera Engine Recommendations</p>
                                    </div>
                                    <button
                                        onClick={handleTick}
                                        disabled={loading.tick}
                                        className="h-8 px-4 linear-gradient text-white text-[10px] font-bold rounded-md flex items-center gap-2 shadow-lg shadow-indigo-500/20 active:scale-95 transition-all uppercase tracking-tight"
                                    >
                                        {loading.tick ? <RefreshCcw size={12} className="animate-spin" /> : 'Run Strategy'}
                                    </button>
                                </div>

                                <div className="space-y-4 min-h-[160px]">
                                    {actions.length === 0 && !loading.tick && (
                                        <div className="border border-dashed border-[#2b2b2f] rounded-lg h-32 flex flex-col items-center justify-center p-6 text-center">
                                            <p className="text-xs text-[#4a4b50] font-medium leading-relaxed">No actions calculated.<br />Hydrate context and run strategy.</p>
                                        </div>
                                    )}
                                    {actions.map((action, i) => (
                                        <div key={i} className="bg-[#1c1c1f] rounded-lg p-5 border border-[#2b2b2f] animate-in fade-in slide-in-from-top-2">
                                            <div className="flex items-center justify-between mb-3 text-[10px] font-bold">
                                                <span className="text-[#5147e7] uppercase tracking-widest">{action.template_name || 'STRATEGY'}</span>
                                                <span className="text-[#4a4b50] uppercase tracking-widest">ACTNOW</span>
                                            </div>
                                            <p className="text-sm font-medium text-white leading-relaxed mb-4">{action.body}</p>
                                            <div className="flex items-center justify-between">
                                                <button className="text-[11px] font-bold bg-white text-black px-4 py-1.5 rounded hover:bg-[#d1d1d1] transition-colors">{action.cta}</button>
                                                {action.rationale && (
                                                    <div className="group relative">
                                                        <Activity size={14} className="text-[#4a4b50] cursor-help" />
                                                        <div className="hidden group-hover:block absolute bottom-full right-0 mb-3 w-56 p-3 bg-white text-black text-[10px] rounded-md shadow-2xl font-medium leading-relaxed">
                                                            {action.rationale}
                                                        </div>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            </div>

                            <div className="bg-[#151518] border border-[#2b2b2f] rounded-xl flex flex-col h-[340px]">
                                <div className="px-5 py-4 border-b border-[#2b2b2f] flex items-center justify-between">
                                    <h3 className="text-sm font-bold text-white tracking-tight">Simulator</h3>
                                    <div className="flex gap-1">
                                        <div className="w-1.5 h-1.5 rounded-full bg-blue-500 opacity-50" />
                                        <div className="w-1.5 h-1.5 rounded-full bg-blue-500 opacity-30" />
                                    </div>
                                </div>

                                <div className="flex-1 p-5 overflow-y-auto space-y-4">
                                    {chat.map((msg, i) => (
                                        <div key={i} className={`flex ${msg.role === 'merchant' ? 'justify-end' : 'justify-start'}`}>
                                            <div className={`max-w-[85%] px-4 py-2.5 rounded-lg text-xs font-medium leading-relaxed ${msg.role === 'merchant'
                                                ? 'bg-[#5147e7] text-white shadow-lg shadow-indigo-500/10'
                                                : 'bg-[#1c1c1f] text-[#eeeeee] border border-[#2b2b2f]'
                                                }`}>
                                                {msg.text}
                                            </div>
                                        </div>
                                    ))}
                                    {chat.length === 0 && <p className="text-center text-[#4a4b50] text-[11px] mt-12 font-medium">Waiting for merchant input...</p>}
                                </div>

                                <div className="p-4 bg-[#1c1c1f] rounded-b-xl border-t border-[#2b2b2f]">
                                    <div className="relative">
                                        <input
                                            value={replyInput}
                                            onChange={(e) => setReplyInput(e.target.value)}
                                            onKeyDown={(e) => e.key === 'Enter' && handleReply()}
                                            disabled={loading.reply}
                                            className="w-full bg-[#0c0c0e] border border-[#2b2b2f] rounded-lg px-4 py-2.5 text-xs text-white outline-none focus:border-[#5147e7] transition-colors placeholder-[#4a4b50]"
                                            placeholder="Simulate reply..."
                                        />
                                        <button onClick={handleReply} className="absolute right-2 top-1/2 -translate-y-1/2 text-[#8a8b91] hover:text-white">
                                            <Send size={14} />
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </main>
        </div>
    );
};

export default App;
