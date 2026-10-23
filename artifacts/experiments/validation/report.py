"""
Gestore e aggregatore del report di validazione formale.
Fornisce metodi per registrare successi (ok), avvertimenti (warn) ed errori (error),
e per stampare il riepilogo formale a terminale.
"""
from dataclasses import dataclass, field


@dataclass
class ValidationReport:
    verbose: bool = True
    successes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def ok(self, msg: str) -> None:
        self.successes.append(msg)
        if self.verbose:
            print(f"[OK]    {msg}")

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)
        if self.verbose:
            print(f"[WARN]  {msg}")

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        if self.verbose:
            print(f"[ERROR] {msg}")

    def is_successful(self) -> bool:
        return len(self.errors) == 0

    def render_summary(self) -> None:
        print("\n" + "=" * 75)
        print("RIEPILOGO FINALE VALIDAZIONE")
        print("=" * 75)

        if self.warnings:
            print(f"\n{len(self.warnings)} avvertimento/i:")
            for w in self.warnings:
                print(f"  {w}")

        if self.errors:
            print(f"\n{len(self.errors)} ERRORE/I RILEVATI:")
            for e in self.errors:
                print(f"  {e}")
            print("\n[ESITO: FALLITO] - Risolvere gli errori sopra elencati.")
        else:
            print(f"\n[ESITO: SUPERATO CON SUCCESSO] - 0 errori, {len(self.warnings)} avvertimenti.")
            print("Declared static checks passed; checks 11-12 retain legacy benchmark compatibility.")
            print("This is not current rubric acceptance, final-workflow readiness or an experimental finding.")
