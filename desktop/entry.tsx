// Desktop entry: put React where the panel's JSX expects it (window.SP_REACT),
// then mount HHC Desktop with the plugin's title and panel as its Controls page.
import "./globals";
import { createRoot } from "react-dom/client";
import plugin from "../src/index";
import { App } from "./app";

const p = (plugin as unknown as () => { titleView: any; content: any; icon: any })();
createRoot(document.getElementById("app")!).render(<App title={p.titleView} panel={p.content} />);
