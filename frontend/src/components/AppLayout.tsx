import { useEffect, useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';
import { Activity, BookOpenText, GitBranch, HardDrive, LayoutDashboard, ShieldAlert } from 'lucide-react';

const navigation = [
  { to: '/', label: '总览', icon: LayoutDashboard, end: true },
  { to: '/traces', label: '审计链路', icon: GitBranch },
  { to: '/risks', label: '风险中心', icon: ShieldAlert },
  { to: '/guide', label: '接入指南', icon: BookOpenText },
];

export function AppLayout() {
  const [stored, setStored] = useState(false);
  useEffect(() => {
    try { localStorage.setItem('agenttrace.workspace', 'active'); setStored(true); } catch { setStored(false); }
  }, []);
  return (
    <div className="min-h-screen bg-[#f5f7fa] lg:flex">
      <aside className="relative z-20 flex shrink-0 flex-col bg-ink-900 text-slate-300 lg:fixed lg:inset-y-0 lg:left-0 lg:w-[232px]">
        <div className="flex h-[76px] items-center gap-3 border-b border-white/[.07] px-5">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-teal-300/20 bg-teal-400/10 text-teal-300"><Activity size={21} strokeWidth={2.2} /></div>
          <div><div className="text-[17px] font-semibold tracking-tight text-white">AgentTrace</div><div className="mt-0.5 text-[10px] tracking-wide text-slate-400">Agent 审计溯源平台</div></div>
        </div>
        <nav aria-label="主导航" className="flex gap-1 overflow-x-auto p-3 lg:mt-4 lg:flex-col lg:overflow-visible lg:px-3">
          {navigation.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} className={({ isActive }) => `flex h-11 shrink-0 items-center gap-3 rounded-lg px-3.5 text-sm font-medium transition-colors lg:w-full ${isActive ? 'bg-teal-400/[.13] text-teal-300 shadow-[inset_2px_0_0_#2dd4bf]' : 'text-slate-400 hover:bg-white/[.05] hover:text-slate-100'}`}><Icon size={17} strokeWidth={1.8} /><span>{label}</span></NavLink>)}
        </nav>
        <div className="mt-auto hidden px-5 pb-5 lg:block">
          <div className="rounded-lg border border-white/[.07] bg-black/10 p-3.5">
            <div className="flex items-center gap-2 text-xs font-medium text-slate-300"><HardDrive size={14} className="text-slate-500" />本地工作区</div>
            <div className="mt-2 flex items-center gap-2 text-[11px] text-slate-500"><span className={`h-1.5 w-1.5 rounded-full ${stored ? 'bg-teal-400' : 'bg-amber-400'}`} />{stored ? '界面状态已保存' : '本地存储不可用'}</div>
          </div>
          <p className="mt-4 text-[10px] leading-4 text-slate-600">让每一次模型决策与工具调用<br />都有迹可循。</p>
        </div>
      </aside>
      <main className="min-w-0 flex-1 lg:ml-[232px]"><Outlet /></main>
    </div>
  );
}
