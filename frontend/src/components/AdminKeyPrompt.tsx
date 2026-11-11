import { useEffect, useState } from 'react';
import { KeyRound } from 'lucide-react';
import { clearAdminKey, getAdminKey, setAdminKey } from '../lib/adminKey';
import { Modal } from './ui';

export function AdminKeyPrompt({ open, onClose, onSaved }: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [value, setValue] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setValue(getAdminKey());
    setError('');
    setBusy(false);
  }, [open]);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    const candidate = value.trim();
    if (!candidate) {
      setError('请输入管理密钥');
      return;
    }
    if (![...candidate].every((char) => char.charCodeAt(0) >= 0x20 && char.charCodeAt(0) <= 0x7e)) {
      setError('管理密钥只能使用 ASCII 字符（HTTP 请求头限制）');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const response = await fetch('/api/traces?page_size=1', {
        headers: { 'X-Admin-Key': candidate },
        cache: 'no-store',
      });
      if (!response.ok) {
        setError(response.status === 401 ? '管理密钥无效' : `密钥验证失败（${response.status}）`);
        return;
      }
      setAdminKey(candidate);
      onSaved();
    } catch {
      setError('无法连接审计服务，请检查后端是否启动。');
    } finally {
      setBusy(false);
    }
  };

  const remove = () => {
    clearAdminKey();
    onSaved();
  };

  if (!open) return null;
  return (
    <Modal title="管理密钥设置" onClose={onClose}>
      <form className="space-y-4" onSubmit={save}>
        <div className="flex items-start gap-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs leading-5 text-blue-700">
          <KeyRound size={16} className="mt-0.5 shrink-0" />
          <p>后端设置了 <code className="rounded bg-white/70 px-1">ADMIN_KEY</code> 时，需要先在这里验证密钥才能查看链路、评论和下载报告。密钥只保存在当前浏览器标签页，关闭标签页后自动清除。</p>
        </div>
        <div>
          <label htmlFor="admin-key" className="mb-1.5 block text-sm font-medium text-slate-700">管理密钥</label>
          <input
            id="admin-key"
            className="input h-10"
            type="password"
            autoComplete="off"
            autoFocus
            value={value}
            onChange={(event) => setValue(event.target.value)}
            placeholder="输入后端 .env 中的 ADMIN_KEY"
          />
        </div>
        {error && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{error}</div>}
        <div className="flex items-center justify-end gap-2 border-t border-slate-100 pt-4">
          {getAdminKey() && <button type="button" className="btn-secondary" onClick={remove}>清除密钥</button>}
          <button type="button" className="btn-secondary" onClick={onClose}>取消</button>
          <button type="submit" className="btn-primary" disabled={busy}>{busy ? '正在验证…' : '验证并保存'}</button>
        </div>
      </form>
    </Modal>
  );
}
