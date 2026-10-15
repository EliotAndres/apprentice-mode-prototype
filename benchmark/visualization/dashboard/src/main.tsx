import { observer } from "mobx-react-lite";
import { createRoot } from "react-dom/client";
import "./index.css";
import { store } from "./store";
import { Performance, Tooltip } from "./Performance";
import { Explorer, Lightbox } from "./Explorer";

const TABS = [["performance", "Performance"], ["explorer", "Dataset explorer"]] as const;

const App = observer(() => <>
  <header className="sticky top-0 z-10 bg-band">
    <div className="mx-auto flex max-w-[1180px] flex-wrap items-center gap-x-6 gap-y-3 px-4 py-4 sm:px-5">
      <p className="flex h-10 items-center whitespace-nowrap rounded-full bg-black px-5 pt-0.5 font-serif text-[17px] font-medium leading-none text-white">Apprentice Mode Benchmark</p>
      <nav role="tablist" className="flex gap-0.5 rounded-full bg-nav p-[3px]">
        {TABS.map(([tab, label]) => <button key={tab} role="tab" aria-selected={store.tab === tab} onClick={() => store.setTab(tab)}
          className="cursor-pointer rounded-full px-3.5 py-2.5 text-sm font-medium leading-none hover:bg-white/55
            aria-selected:bg-white aria-selected:shadow-[0_1px_2px_rgba(0,0,0,.06)]">{label}</button>)}
      </nav>
    </div>
  </header>
  <main className="mx-auto max-w-[1180px] px-4 pb-24 pt-12 sm:px-5">
    {store.tab === "performance" ? <Performance /> : <Explorer />}
  </main>
  <Tooltip />
  <Lightbox />
</>);

createRoot(document.getElementById("root")!).render(<App />);
