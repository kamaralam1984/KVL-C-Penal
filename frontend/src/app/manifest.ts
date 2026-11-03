import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "KVL Hosting Panel",
    short_name: "KVL Panel",
    description: "Control panel for KVL private hosting infrastructure.",
    start_url: "/",
    display: "standalone",
    background_color: "#f5f6f8",
    theme_color: "#0b7a85",
    icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "any" }],
  };
}
