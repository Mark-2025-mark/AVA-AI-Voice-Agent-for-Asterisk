import { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
    Building2,
    CheckCircle2,
    Eye,
    EyeOff,
    Link2,
    Loader2,
    Pencil,
    Plus,
    RefreshCw,
    ServerCog,
    Trash2,
    Users,
} from 'lucide-react';
import { toast } from 'sonner';
import { ConfigSection } from '../components/ui/ConfigSection';
import { ConfigCard } from '../components/ui/ConfigCard';
import { Modal } from '../components/ui/Modal';
import { FormInput, FormSelect, FormSwitch } from '../components/ui/FormComponents';
import HelpTooltip from '../components/ui/HelpTooltip';
import { describeApiError } from '../utils/apiErrors';

type Connection = {
    id?: string;
    base_url: string;
    token?: string;
    token_configured?: boolean;
    verify_ssl?: boolean;
    timeout_ms?: number;
    last_verified_at?: string | null;
    last_verification?: { ok?: boolean; status_code?: number };
};

type AgentOption = {
    slug: string;
    display_name: string;
    is_default?: boolean;
};

type Assignment = {
    id: string;
    domain_uuid: string;
    domain_name?: string | null;
    domain_description?: string | null;
    agent_slug: string;
    agent_display_name?: string | null;
    agent_available?: boolean;
    enabled: boolean;
    notes?: string | null;
};

type DomainRow = {
    domain_uuid: string;
    domain_name?: string | null;
    domain_description?: string | null;
    domain_enabled?: boolean;
    assignment?: Assignment | null;
};

type ConnectionForm = {
    base_url: string;
    token: string;
    verify_ssl: boolean;
    timeout_ms: number | string;
};

type AssignmentForm = {
    domain_uuid: string;
    domain_name: string;
    domain_description: string;
    agent_slug: string;
    enabled: boolean;
    notes: string;
};

const emptyConnectionForm = (): ConnectionForm => ({
    base_url: '',
    token: '',
    verify_ssl: true,
    timeout_ms: 10000,
});

const emptyAssignmentForm = (): AssignmentForm => ({
    domain_uuid: '',
    domain_name: '',
    domain_description: '',
    agent_slug: '',
    enabled: true,
    notes: '',
});

const domainLabel = (row: Pick<DomainRow, 'domain_description' | 'domain_name' | 'domain_uuid'>) =>
    row.domain_description || row.domain_name || row.domain_uuid;

const FspbxTenantsPage = () => {
    const [loading, setLoading] = useState(true);
    const [testing, setTesting] = useState(false);
    const [refreshingDomains, setRefreshingDomains] = useState(false);
    const [savingConnection, setSavingConnection] = useState(false);
    const [connection, setConnection] = useState<Connection | null>(null);
    const [connectionForm, setConnectionForm] = useState<ConnectionForm>(emptyConnectionForm());
    const [showToken, setShowToken] = useState(false);
    const [domains, setDomains] = useState<DomainRow[]>([]);
    const [agents, setAgents] = useState<AgentOption[]>([]);
    const [assignmentModalOpen, setAssignmentModalOpen] = useState(false);
    const [editingAssignment, setEditingAssignment] = useState<Assignment | null>(null);
    const [assignmentForm, setAssignmentForm] = useState<AssignmentForm>(emptyAssignmentForm());
    const [savingAssignment, setSavingAssignment] = useState(false);

    const agentOptions = useMemo(
        () =>
            agents.map((agent) => ({
                value: agent.slug,
                label: agent.is_default
                    ? `${agent.display_name} (${agent.slug}) · default`
                    : `${agent.display_name} (${agent.slug})`,
            })),
        [agents],
    );

    const loadAgents = useCallback(async () => {
        const res = await axios.get('/api/fspbx/agents');
        setAgents(res.data?.agents || []);
    }, []);

    const loadConnection = useCallback(async () => {
        const res = await axios.get('/api/fspbx/connection');
        const row = res.data?.connection as Connection | null;
        setConnection(row);
        setConnectionForm({
            base_url: row?.base_url || '',
            token: '',
            verify_ssl: row?.verify_ssl !== false,
            timeout_ms: row?.timeout_ms || 10000,
        });
    }, []);

    const loadDomains = useCallback(async () => {
        setRefreshingDomains(true);
        try {
            const res = await axios.get('/api/fspbx/domains');
            setDomains(res.data?.domains || []);
        } catch (error) {
            setDomains([]);
            throw error;
        } finally {
            setRefreshingDomains(false);
        }
    }, []);

    const refreshAll = useCallback(async () => {
        setLoading(true);
        try {
            await Promise.all([loadConnection(), loadAgents()]);
            try {
                await loadDomains();
            } catch (error) {
                if (axios.isAxiosError(error) && error.response?.status === 409) {
                    setDomains([]);
                } else {
                    throw error;
                }
            }
        } catch (error) {
            toast.error(describeApiError(error, 'Failed to load FS PBX tenant settings'));
        } finally {
            setLoading(false);
        }
    }, [loadAgents, loadConnection, loadDomains]);

    useEffect(() => {
        refreshAll();
    }, [refreshAll]);

    const handleSaveConnection = async () => {
        setSavingConnection(true);
        try {
            const payload: ConnectionForm = { ...connectionForm };
            if (!payload.token.trim() && !connection?.token_configured) {
                toast.error('Paste the FS PBX API Bearer token.');
                return;
            }
            const res = await axios.put('/api/fspbx/connection', {
                base_url: payload.base_url,
                token: payload.token.trim() || undefined,
                verify_ssl: payload.verify_ssl,
                timeout_ms: Number(payload.timeout_ms) || 10000,
            });
            setConnection(res.data?.connection || null);
            setConnectionForm((prev) => ({ ...prev, token: '' }));
            toast.success('FS PBX connection saved');
            await loadDomains();
        } catch (error) {
            toast.error(describeApiError(error, 'Failed to save FS PBX connection'));
        } finally {
            setSavingConnection(false);
        }
    };

    const handleTestConnection = async () => {
        setTesting(true);
        try {
            await axios.post('/api/fspbx/connection/test', {
                base_url: connectionForm.base_url,
                token: connectionForm.token.trim() || undefined,
                verify_ssl: connectionForm.verify_ssl,
                timeout_ms: Number(connectionForm.timeout_ms) || 10000,
            });
            toast.success('FS PBX connection verified');
            await Promise.all([loadConnection(), loadDomains()]);
        } catch (error) {
            toast.error(describeApiError(error, 'FS PBX connection test failed'));
        } finally {
            setTesting(false);
        }
    };

    const openCreateAssignment = (domain?: DomainRow) => {
        setEditingAssignment(null);
        setAssignmentForm({
            domain_uuid: domain?.domain_uuid || '',
            domain_name: domain?.domain_name || '',
            domain_description: domain?.domain_description || '',
            agent_slug: agents.find((agent) => agent.is_default)?.slug || agents[0]?.slug || '',
            enabled: true,
            notes: '',
        });
        setAssignmentModalOpen(true);
    };

    const openEditAssignment = (assignment: Assignment) => {
        setEditingAssignment(assignment);
        setAssignmentForm({
            domain_uuid: assignment.domain_uuid,
            domain_name: assignment.domain_name || '',
            domain_description: assignment.domain_description || '',
            agent_slug: assignment.agent_slug,
            enabled: assignment.enabled,
            notes: assignment.notes || '',
        });
        setAssignmentModalOpen(true);
    };

    const handleSaveAssignment = async () => {
        if (!assignmentForm.domain_uuid || !assignmentForm.agent_slug) {
            toast.error('Select a tenant and an AVA agent.');
            return;
        }
        setSavingAssignment(true);
        try {
            const payload = {
                domain_uuid: assignmentForm.domain_uuid,
                domain_name: assignmentForm.domain_name || null,
                domain_description: assignmentForm.domain_description || null,
                agent_slug: assignmentForm.agent_slug,
                enabled: assignmentForm.enabled,
                notes: assignmentForm.notes || null,
            };
            if (editingAssignment) {
                await axios.patch(`/api/fspbx/assignments/${editingAssignment.id}`, payload);
                toast.success('Tenant assignment updated');
            } else {
                await axios.post('/api/fspbx/assignments', payload);
                toast.success('Tenant assignment created');
            }
            setAssignmentModalOpen(false);
            await loadDomains();
        } catch (error) {
            toast.error(describeApiError(error, 'Failed to save tenant assignment'));
        } finally {
            setSavingAssignment(false);
        }
    };

    const handleDeleteAssignment = async (assignment: Assignment) => {
        if (!window.confirm(`Remove agent assignment for ${domainLabel(assignment)}?`)) {
            return;
        }
        try {
            await axios.delete(`/api/fspbx/assignments/${assignment.id}`);
            toast.success('Tenant assignment removed');
            await loadDomains();
        } catch (error) {
            toast.error(describeApiError(error, 'Failed to delete tenant assignment'));
        }
    };

    const assignedCount = domains.filter((row) => row.assignment).length;

    return (
        <div className="space-y-6">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
                        <Building2 className="w-6 h-6 text-primary" />
                        FS PBX Tenants
                    </h1>
                    <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
                        Connect to FS PBX API v1 and assign each tenant domain to an AVA agent.
                        This mapping is the operator source of truth for multi-tenant routing.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={refreshAll}
                    disabled={loading || refreshingDomains}
                    className="inline-flex items-center gap-2 px-3 py-2 text-sm rounded-md border border-border hover:bg-accent"
                >
                    <RefreshCw className={`w-4 h-4 ${loading || refreshingDomains ? 'animate-spin' : ''}`} />
                    Refresh
                </button>
            </div>

            <ConfigSection
                title="FS PBX Connection"
                description="Uses the public FS PBX API v1 with a Bearer token (same auth model as Centralita)."
            >
                <ConfigCard>
                    <div className="grid gap-4 md:grid-cols-2">
                        <FormInput
                            label="FS PBX URL"
                            value={connectionForm.base_url}
                            onChange={(e) => setConnectionForm((prev) => ({ ...prev, base_url: e.target.value }))}
                            placeholder="https://pbx.example.com"
                            tooltip="Base URL of your FS PBX server, without /api/v1."
                        />
                        <div className="relative">
                            <FormInput
                                label="API Bearer Token"
                                type={showToken ? 'text' : 'password'}
                                value={connectionForm.token}
                                onChange={(e) => setConnectionForm((prev) => ({ ...prev, token: e.target.value }))}
                                placeholder={connection?.token_configured ? 'Leave blank to keep saved token' : 'Paste Sanctum token'}
                                tooltip="Create a token in FS PBX Admin → API Tokens. Requires domain list permissions."
                                className="pr-10"
                            />
                            <button
                                type="button"
                                onClick={() => setShowToken((value) => !value)}
                                className="absolute right-3 top-[34px] text-muted-foreground hover:text-foreground"
                                aria-label={showToken ? 'Hide token' : 'Show token'}
                            >
                                {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                            </button>
                        </div>
                        <FormInput
                            label="Timeout (ms)"
                            type="number"
                            value={String(connectionForm.timeout_ms)}
                            onChange={(e) => setConnectionForm((prev) => ({ ...prev, timeout_ms: e.target.value }))}
                        />
                        <div className="flex items-end">
                            <FormSwitch
                                label="Verify SSL"
                                checked={connectionForm.verify_ssl}
                                onChange={(e) =>
                                    setConnectionForm((prev) => ({ ...prev, verify_ssl: e.target.checked }))
                                }
                            />
                        </div>
                    </div>

                    <div className="mt-4 flex flex-wrap items-center gap-3">
                        <button
                            type="button"
                            onClick={handleSaveConnection}
                            disabled={savingConnection}
                            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
                        >
                            {savingConnection ? <Loader2 className="w-4 h-4 animate-spin" /> : <ServerCog className="w-4 h-4" />}
                            Save Connection
                        </button>
                        <button
                            type="button"
                            onClick={handleTestConnection}
                            disabled={testing}
                            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md border border-border hover:bg-accent disabled:opacity-60"
                        >
                            {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Link2 className="w-4 h-4" />}
                            Test Connection
                        </button>
                        {connection?.last_verified_at && (
                            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                                <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                                Last verified {new Date(connection.last_verified_at).toLocaleString()}
                            </span>
                        )}
                    </div>
                </ConfigCard>
            </ConfigSection>

            <ConfigSection
                title="Tenant Assignments"
                description={`${assignedCount} of ${domains.length} tenants mapped to AVA agents.`}
            >
                <div className="flex justify-end">
                    <button
                        type="button"
                        onClick={() => openCreateAssignment()}
                        disabled={!connection?.token_configured || agents.length === 0}
                        className="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
                    >
                        <Plus className="w-4 h-4" />
                        Assign Tenant
                    </button>
                </div>
                <ConfigCard>
                    {loading ? (
                        <div className="flex items-center justify-center py-16 text-muted-foreground">
                            <Loader2 className="w-5 h-5 animate-spin mr-2" />
                            Loading tenants...
                        </div>
                    ) : !connection?.token_configured ? (
                        <div className="py-10 text-center text-sm text-muted-foreground">
                            Save an FS PBX connection above to load tenant domains.
                        </div>
                    ) : domains.length === 0 ? (
                        <div className="py-10 text-center text-sm text-muted-foreground">
                            No FS PBX domains returned for this token.
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <table className="min-w-full text-sm">
                                <thead>
                                    <tr className="border-b border-border text-left text-muted-foreground">
                                        <th className="py-3 pr-4 font-medium">Tenant</th>
                                        <th className="py-3 pr-4 font-medium">Domain UUID</th>
                                        <th className="py-3 pr-4 font-medium">AVA Agent</th>
                                        <th className="py-3 pr-4 font-medium">Status</th>
                                        <th className="py-3 text-right font-medium">Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {domains.map((row) => {
                                        const assignment = row.assignment;
                                        return (
                                            <tr key={row.domain_uuid} className="border-b border-border/60">
                                                <td className="py-3 pr-4">
                                                    <div className="font-medium">{domainLabel(row)}</div>
                                                    {row.domain_name && row.domain_description && (
                                                        <div className="text-xs text-muted-foreground">{row.domain_name}</div>
                                                    )}
                                                </td>
                                                <td className="py-3 pr-4 font-mono text-xs">{row.domain_uuid}</td>
                                                <td className="py-3 pr-4">
                                                    {assignment ? (
                                                        <div className="flex items-center gap-2">
                                                            <Users className="w-4 h-4 text-primary" />
                                                            <span>
                                                                {assignment.agent_display_name || assignment.agent_slug}
                                                                <span className="text-muted-foreground"> ({assignment.agent_slug})</span>
                                                            </span>
                                                        </div>
                                                    ) : (
                                                        <span className="text-muted-foreground">Not assigned</span>
                                                    )}
                                                </td>
                                                <td className="py-3 pr-4">
                                                    {assignment ? (
                                                        <span
                                                            className={`inline-flex px-2 py-0.5 rounded-full text-xs ${
                                                                assignment.enabled && assignment.agent_available
                                                                    ? 'bg-emerald-500/10 text-emerald-600'
                                                                    : 'bg-amber-500/10 text-amber-700'
                                                            }`}
                                                        >
                                                            {assignment.enabled
                                                                ? assignment.agent_available
                                                                    ? 'Active'
                                                                    : 'Agent missing'
                                                                : 'Disabled'}
                                                        </span>
                                                    ) : (
                                                        <span className="text-xs text-muted-foreground">—</span>
                                                    )}
                                                </td>
                                                <td className="py-3 text-right">
                                                    <div className="inline-flex items-center gap-2">
                                                        {assignment ? (
                                                            <>
                                                                <button
                                                                    type="button"
                                                                    onClick={() => openEditAssignment(assignment)}
                                                                    className="p-2 rounded-md hover:bg-accent"
                                                                    title="Edit assignment"
                                                                >
                                                                    <Pencil className="w-4 h-4" />
                                                                </button>
                                                                <button
                                                                    type="button"
                                                                    onClick={() => handleDeleteAssignment(assignment)}
                                                                    className="p-2 rounded-md hover:bg-destructive/10 text-destructive"
                                                                    title="Remove assignment"
                                                                >
                                                                    <Trash2 className="w-4 h-4" />
                                                                </button>
                                                            </>
                                                        ) : (
                                                            <button
                                                                type="button"
                                                                onClick={() => openCreateAssignment(row)}
                                                                className="inline-flex items-center gap-1 px-3 py-1.5 rounded-md border border-border hover:bg-accent text-xs"
                                                            >
                                                                <Plus className="w-3.5 h-3.5" />
                                                                Assign
                                                            </button>
                                                        )}
                                                    </div>
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </ConfigCard>
            </ConfigSection>

            <Modal
                isOpen={assignmentModalOpen}
                onClose={() => setAssignmentModalOpen(false)}
                title={editingAssignment ? 'Edit Tenant Assignment' : 'Assign Tenant to Agent'}
            >
                <div className="space-y-4">
                    {editingAssignment ? (
                        <FormInput label="Tenant" value={domainLabel(assignmentForm)} disabled />
                    ) : (
                        <FormSelect
                            label="FS PBX Tenant"
                            value={assignmentForm.domain_uuid}
                            onChange={(e) => {
                                const value = e.target.value;
                                const selected = domains.find((row) => row.domain_uuid === value);
                                setAssignmentForm((prev) => ({
                                    ...prev,
                                    domain_uuid: value,
                                    domain_name: selected?.domain_name || '',
                                    domain_description: selected?.domain_description || '',
                                }));
                            }}
                            options={domains.map((row) => ({
                                value: row.domain_uuid,
                                label: domainLabel(row),
                            }))}
                        />
                    )}

                    <FormSelect
                        label="AVA Agent"
                        value={assignmentForm.agent_slug}
                        onChange={(e) =>
                            setAssignmentForm((prev) => ({ ...prev, agent_slug: e.target.value }))
                        }
                        options={agentOptions}
                    />

                    <FormSwitch
                        label="Enabled"
                        checked={assignmentForm.enabled}
                        onChange={(e) =>
                            setAssignmentForm((prev) => ({ ...prev, enabled: e.target.checked }))
                        }
                    />

                    <div className="mb-4">
                        <label className="block text-sm font-medium mb-1.5" htmlFor="assignment-notes">
                            Notes
                        </label>
                        <textarea
                            id="assignment-notes"
                            value={assignmentForm.notes}
                            onChange={(e) => setAssignmentForm((prev) => ({ ...prev, notes: e.target.value }))}
                            placeholder="Optional operator notes for this tenant mapping"
                            rows={3}
                            className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-ring"
                        />
                    </div>

                    <div className="rounded-md border border-border/70 bg-muted/30 p-3 text-xs text-muted-foreground flex items-start gap-2">
                        <HelpTooltip
                            ariaLabel="Tenant mapping help"
                            content="Dialplan routing still sets AI_AGENT on the Asterisk side. This mapping documents which AVA agent belongs to each FS PBX tenant for provisioning and ERP integration."
                        />
                        <span>
                            Use the agent slug in Asterisk dialplan (<code>AI_AGENT=slug</code>) when routing calls from FS PBX.
                        </span>
                    </div>

                    <div className="flex justify-end gap-2 pt-2">
                        <button
                            type="button"
                            onClick={() => setAssignmentModalOpen(false)}
                            className="px-4 py-2 text-sm rounded-md border border-border hover:bg-accent"
                        >
                            Cancel
                        </button>
                        <button
                            type="button"
                            onClick={handleSaveAssignment}
                            disabled={savingAssignment}
                            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium rounded-md bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
                        >
                            {savingAssignment && <Loader2 className="w-4 h-4 animate-spin" />}
                            Save Assignment
                        </button>
                    </div>
                </div>
            </Modal>
        </div>
    );
};

export default FspbxTenantsPage;
