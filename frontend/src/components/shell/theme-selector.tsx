"use client";

import * as React from "react";
import { useTheme } from "next-themes";
import { Check, Monitor, Moon, Sun } from "lucide-react";
import { IconButton } from "@/components/ui/button";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger } from "@/components/ui/menu";

const OPTIONS = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
] as const;

export function ThemeSelector() {
  const { theme, resolvedTheme, setTheme } = useTheme();
  // next-themes only knows the theme on the client; render a stable icon until mounted.
  const mounted = React.useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
  const Icon = mounted && resolvedTheme === "dark" ? Moon : Sun;

  return (
    <Menu>
      <MenuTrigger asChild>
        <IconButton label="Change theme">
          <Icon className="h-[18px] w-[18px]" />
        </IconButton>
      </MenuTrigger>
      <MenuContent>
        <MenuLabel>Theme</MenuLabel>
        {OPTIONS.map((o) => (
          <MenuItem key={o.value} icon={o.icon} onSelect={() => setTheme(o.value)}>
            {o.label}
            {mounted && theme === o.value && <Check className="ml-auto h-4 w-4 text-primary" aria-label="Selected" />}
          </MenuItem>
        ))}
      </MenuContent>
    </Menu>
  );
}
