# HHC's UMAF display engine

`py_modules/hhctl/data/umaf-DisplayEngine.efi` replaces Smokeless UMAF's `DisplayEngine.efi`: the same menus with a
themed console (Limine's font, HHC's icon and label), a plain-language explanation under every setting and a list of
what was changed before saving.

Built from EDK2 edk2-stable202608 with `displayengine-theme.patch` (MdeModulePkg/Universal/DisplayEngineDxe):
set `#define THEME_HHC 1` in `ThemeVariant.h`, then

    . edksetup.sh
    build -a X64 -t GCC -b RELEASE -p MdeModulePkg/MdeModulePkg.dsc -m MdeModulePkg/Universal/DisplayEngineDxe/DisplayEngineDxe.inf

UMAF itself (UMAF.efi / SetupBrowser.efi / UiApp.efi) is downloaded at install time from
github.com/DavidS95/Smokeless_UMAF and checked by its SHA-256 (see `py_modules/hhctl/umaf.py`).
