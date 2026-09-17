import postcss from "postcss";
import tw from "@tailwindcss/postcss";

const css = '@import "@cloudpartnerops/ui/styles.css";\n.flex { display: flex; }';
try {
  const r = await postcss([tw()]).process(css, { from: "src/app/globals.css" });
  const out = r.css;
  console.log("OK len:", out.length);
  console.log("has .rounded-lg:", out.includes(".rounded-lg"));
  console.log("has .flex{:", /\.flex\s*\{/.test(out));
  console.log("raw @theme remains:", out.includes("@theme"));
} catch (e) {
  console.log("ERR:", e.message.slice(0, 400));
}
