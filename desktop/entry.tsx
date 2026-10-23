// Desktop entry: put React where the panel's JSX expects it (window.SP_REACT),
// then mount the plugin's title and panel like Decky's Quick Access tab.
import "./globals";
import { createRoot } from "react-dom/client";
import plugin from "../src/index";

const p = (plugin as unknown as () => { titleView: any; content: any; icon: any })();
createRoot(document.getElementById("title")!).render(p.titleView);
createRoot(document.getElementById("panel")!).render(p.content);
