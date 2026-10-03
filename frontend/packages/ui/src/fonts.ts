/**
 * Self-hosted fonts (SPEC §8.1): Inter for Latin and IBM Plex Sans Arabic for
 * Arabic, both OFL-1.1 and served from our own origin (no font CDN at runtime).
 *
 * Each app imports this once from its entry point. The @font-face rules carry
 * unicode-range subsets, so browsers download only the scripts a page uses.
 */
import "@fontsource-variable/inter/wght.css";
import "@fontsource/ibm-plex-sans-arabic/400.css";
import "@fontsource/ibm-plex-sans-arabic/500.css";
import "@fontsource/ibm-plex-sans-arabic/600.css";
import "@fontsource/ibm-plex-sans-arabic/700.css";
