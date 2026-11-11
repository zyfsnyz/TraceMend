import { useEffect, useState, type ReactNode } from 'react';
import { AlertCircle, Check, Clipboard, Inbox, LoaderCircle, X } from 'lucide-react';
import { stringifySafe } from '../lib/sensitive';
import type { JsonValue } from '../api/types';

export function Spinner({ label = '正在加载审计数据' }: { label?: string }) {
  return <div className="flex min-h-56 flex-col items-center justify-center gap-3 text-slate-500" role="status"><LoaderCircle className="animate-spin text-teal-600" size={25} /><span className="text-sm">{label}</span></div>;
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <div className="m-5 flex items-start gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700" role="alert"><AlertCircle className="mt-0.5 shrink-0" size={18} /><div className="flex-1"><p className="font-medium">数据加载失败</p><p className="mt-1 leading-5 text-red-600">{message}</p></div>{onRetry && <button className="btn-secondary h-8 text-xs" onClick={onRetry}>重新加载</button>}</div>;
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="flex min-h-52 flex-col items-center justify-center px-6 py-10 text-center"><div className="mb-3 rounded-xl border border-slate-200 bg-slate-50 p-3 text-slate-400"><Inbox size={23} /></div><p className="text-sm font-medium text-slate-700">{title}</p><p className="mt-1 max-w-sm text-xs leading-5 text-slate-400">{description}</p>{action && <div className="mt-4">{action}</div>}</div>;
}

export function StatusBadge({ status }: { status: string }) {
  const styles: Record<string, string> = {
    completed: 'border-teal-200 bg-teal-50 text-teal-700', running: 'border-blue-200 bg-blue-50 text-blue-700',
    error: 'border-red-200 bg-red-50 text-red-700', failed: 'border-red-200 bg-red-50 text-red-700',
    open: 'border-amber-200 bg-amber-50 text-amber-700', resolved: 'border-slate-200 bg-slate-100 text-slate-600',
  };
  const labels: Record<string, string> = { completed: '已完成', running: '运行中', error: '异常', failed: '失败', open: '待处理', resolved: '已解决' };
  return <span className={`badge ${styles[status] ?? 'border-slate-200 bg-slate-50 text-slate-600'}`}><span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />{labels[status] ?? status}</span>;
}

export function SeverityBadge({ severity }: { severity: string }) {
  const styles: Record<string, string> = {
    critical: 'border-red-300 bg-red-100 text-red-800', high: 'border-red-200 bg-red-50 text-red-700',
    medium: 'border-amber-200 bg-amber-50 text-amber-700', low: 'border-blue-200 bg-blue-50 text-blue-700',
  };
  const labels: Record<string, string> = { critical: '严重', high: '高风险', medium: '中风险', low: '低风险' };
  return <span className={`badge ${styles[severity] ?? styles.low}`}>{labels[severity] ?? severity}</span>;
}

export function JsonBlock({ value }: { value: JsonValue | Record<string, unknown> | undefined }) {
  return <pre className="code-block">{stringifySafe(value)}</pre>;
}

export function CopyButton({ text, label = '复制' }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = document.createElement('textarea');
      area.value = text; area.style.position = 'fixed'; area.style.opacity = '0';
      document.body.appendChild(area); area.select(); document.execCommand('copy'); area.remove();
    }
    setCopied(true);
  };
  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1800);
    return () => window.clearTimeout(timer);
  }, [copied]);
  return <button className="btn-secondary h-8 px-2.5 text-xs" onClick={copy}>{copied ? <Check size={13} className="text-teal-600" /> : <Clipboard size={13} />}{copied ? '已复制' : label}</button>;
}

export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/45 p-4" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><div className="w-full max-w-md rounded-xl border border-white/20 bg-white shadow-float" role="dialog" aria-modal="true" aria-label={title}><div className="flex items-center justify-between border-b border-slate-100 px-5 py-4"><h2 className="font-semibold text-ink-900">{title}</h2><button className="icon-button h-8 w-8" aria-label="关闭" onClick={onClose}><X size={18} /></button></div><div className="p-5">{children}</div></div></div>;
}

export function InlineNotice({ tone = 'info', children }: { tone?: 'info' | 'success' | 'error'; children: ReactNode }) {
  const styles = { info: 'border-blue-200 bg-blue-50 text-blue-700', success: 'border-teal-200 bg-teal-50 text-teal-700', error: 'border-red-200 bg-red-50 text-red-700' };
  return <div className="rounded-lg border px-3.5 py-2.5 text-sm leading-5" role="status"><div className={`${styles[tone]} -m-3.5 -my-2.5 rounded-lg border px-3.5 py-2.5`}>{children}</div></div>;
}
