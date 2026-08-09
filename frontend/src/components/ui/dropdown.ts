import { useLayoutEffect, useRef, useState } from "react";

export function useDropdownFlip(open: boolean) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [openUp, setOpenUp] = useState(false);

  useLayoutEffect(() => {
    if (!open) return;
    const el = menuRef.current;
    if (!el) return;
    setOpenUp(el.getBoundingClientRect().bottom > window.innerHeight - 8);
  }, [open]);

  return { menuRef, openUp };
}
