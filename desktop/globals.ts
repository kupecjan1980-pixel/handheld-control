// Imported first by entry.tsx, so it runs before the panel's modules.
import * as React from "react";
import * as ReactDOM from "react-dom";

(window as any).SP_REACT = React;
(window as any).SP_REACTDOM = ReactDOM;
// The panel's title reads this to say "HHC Desktop" instead of "HHC".
(window as any).HHC_DESKTOP = true;
