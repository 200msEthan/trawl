# The Trawl playground

`index.html` is one page that runs Trawl in the browser: the reference interpreter
(`trawl/lang.py`, `trawl/interp.py` and the libraries) runs on [Pyodide](https://pyodide.org),
which is Python compiled to WebAssembly. Nothing gets installed, and no server runs any code.

- **Open it locally:** open `playground/index.html` in a browser.
- **Host it:** put `index.html` on any static host (GitHub Pages, Cloudflare Pages and so on).
  It's a single file. Its only outside request is Pyodide 0.26.4, loaded from jsDelivr.
- **Rebuild it** after changing the interpreter, a library or an example:
  `python playground/build.py`. The page embeds those files, and a test fails if it's out of date.

Every printed line that starts with a number shows up as a catch; a line that starts with `= ` shows as a
highlighted summary card instead, and isn't counted. Programs run in a background worker. **Stop** ends a run right away, and the step limit
(20 million) and output limit (500,000 characters) keep a runaway program from freezing the tab.
The playground is the interpreter, so it is much slower than `trawl fast`. For real searches,
install Trawl.
