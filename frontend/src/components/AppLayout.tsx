import { useEffect, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { Activity, BookOpenText, GitBranch, HardDrive, KeyRound, LayoutDashboard, Settings2, ShieldAlert, ShieldCheck } from 'lucide-react';
import { UNAUTHORIZED_EVENT } from '../lib/adminKey';
import { AdminKeyPrompt } from './AdminKeyPrompt';

const navigation = [
  { to: '/', label: '总览', icon: LayoutDashboard, end: true },
  { to: '/traces', label: '审计链路', icon: GitBranch },
  { to: '/risks', label: '风险中心', icon: ShieldAlert },
  { to: '/settings', label: '连接设置', icon: Settings2 },
  { to: '/guide', label: '接入指南', icon: BookOpenText },
];

export function AppLayout() {
  const location = useLocation();
  const currentPage = navigation.find((item) => item.end ? location.pathname === item.to : location.pathname.startsWith(item.to)) ?? navigation[0];
  const navRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const nav = navRef.current;
    const active = nav?.querySelector<HTMLElement>('[aria-current="page"]');
    if (!nav || !active || nav.scrollWidth <= nav.clientWidth) return;
    nav.scrollTo({ left: Math.max(0, active.offsetLeft - (nav.clientWidth - active.clientWidth) / 2), behavior: 'auto' });
  }, [location.pathname]);
  const [stored, setStored] = useState(false);
  const [adminOpen, setAdminOpen] = useState(false);
  const [pageRevision, setPageRevision] = useState(0);
  useEffect(() => {
    try { localStorage.setItem('agenttrace.workspace', 'active'); setStored(true); } catch { setStored(false); }
  }, []);
  useEffect(() => {
    const openAdmin = () => setAdminOpen(true);
    window.addEventListener(UNAUTHORIZED_EVENT, openAdmin);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, openAdmin);
  }, []);
  return (
    <div className="min-h-screen bg-transparent lg:flex">
      <aside className="relative z-20 flex shrink-0 flex-col border-r border-white/[.04] bg-[#111b28] text-slate-300 lg:fixed lg:inset-y-0 lg:left-0 lg:w-[232px]">
        <div className="flex h-[76px] items-center gap-3 border-b border-white/[.07] px-5">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-teal-300/20 bg-teal-400/10 text-teal-300 shadow-[0_0_24px_rgba(45,212,191,.06)]"><Activity size={21} strokeWidth={2.2} /></div>
          <div><div className="text-[17px] font-semibold tracking-[-0.02em] text-white">AgentTrace</div><div className="mt-0.5 text-[10px] tracking-wide text-slate-500">Agent 审计溯源平台</div></div>
        </div>
        <nav ref={navRef} aria-label="主导航" className="nav-scroll flex gap-1 overflow-x-auto p-3 lg:mt-3 lg:flex-col lg:overflow-visible lg:px-3">
          <p className="hidden px-3.5 pb-2 pt-1 text-[10px] font-medium uppercase tracking-[0.14em] text-slate-600 lg:block">工作区</p>
          {navigation.map(({ to, label, icon: Icon, end }) => <NavLink key={to} to={to} end={end} className={({ isActive }) => `flex h-[42px] shrink-0 items-center gap-3 rounded-[10px] border px-3.5 text-[13px] font-medium transition-colors lg:w-full ${isActive ? 'border-teal-300/10 bg-teal-400/[.12] text-teal-300 shadow-[inset_2px_0_0_#2dd4bf]' : 'border-transparent text-slate-400 hover:bg-white/[.045] hover:text-slate-100'}`}><Icon size={17} strokeWidth={1.8} /><span>{label}</span></NavLink>)}
          <button type="button" className="ml-auto flex h-11 shrink-0 items-center justify-center rounded-lg px-3 text-slate-500 hover:bg-white/[.05] hover:text-teal-300 lg:hidden" aria-label="管理密钥" onClick={() => setAdminOpen(true)}><KeyRound size={18} /></button>
        </nav>
        <div className="mt-auto hidden px-5 pb-5 lg:block">
          <div className="rounded-xl border border-white/[.08] bg-white/[.035] p-3.5">
            <div className="flex items-center gap-2 text-xs font-medium text-slate-300"><HardDrive size={14} className="text-slate-500" />本地工作区</div>
            <div className="mt-2 flex items-center gap-2 text-[11px] text-slate-500"><span className={`h-1.5 w-1.5 rounded-full ${stored ? 'bg-teal-400' : 'bg-amber-400'}`} />{stored ? '界面状态已保存' : '本地存储不可用'}</div>
            <button type="button" className="mt-3 flex items-center gap-1.5 text-[11px] text-slate-400 transition-colors hover:text-teal-300" onClick={() => setAdminOpen(true)}><KeyRound size={13} />管理密钥</button>
          </div>
          <p className="mt-4 text-[10px] leading-4 text-slate-500">让每一次模型决策与工具调用<br />都有迹可循。</p>
        </div>
      </aside>
      <main className="min-w-0 flex-1 lg:ml-[232px]">
        <div className="sticky top-0 z-10 flex h-[52px] items-center justify-between border-b border-[#dfe6e8]/80 bg-white/85 px-5 py-2.5 backdrop-blur-xl md:px-7">
          <div className="flex items-center gap-2 text-[11px]"><span className="text-slate-400">工作区</span><span className="text-slate-300">/</span><span className="font-medium text-slate-700">{currentPage.label}</span></div>
          <div className="flex items-center gap-4"><span className="hidden items-center gap-1.5 text-[11px] text-slate-500 sm:flex"><ShieldCheck size={13} className="text-teal-600" />本地优先 · 数据留在当前工作区</span><NavLink to="/settings" className="flex items-center gap-1.5 text-[11px] font-medium text-slate-500 transition-colors hover:text-teal-700"><Settings2 size={13} />模型与工具连接</NavLink></div>
        </div>
        <div key={pageRevision}><Outlet /></div>
      </main>
      <AdminKeyPrompt open={adminOpen} onClose={() => setAdminOpen(false)} onSaved={() => { setAdminOpen(false); setPageRevision((value) => value + 1); }} />
    </div>
  );
}
