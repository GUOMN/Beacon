/** A bundled emote's slug, or 'c:' + 32 lower-case hex characters (the hash of an imported emote). */
export type EmoteRef = string;

export interface CustomEmote {
  id: EmoteRef;
  name: string;
  /** Has a `.anim` file besides the still. */
  animated: boolean;
  /** Has a `.sound` file: it plays when we place the emote, on this computer only (rooms never get it). */
  sound?: boolean;
  /** Which signature effect it plays when it lands: 0–5. Nothing stored (an entry from an older version) plays the plain pop. */
  fx?: number;
}

/** A stored effect: an integer within EMOTE_FX_VARIANTS. Anything else means the plain pop. */
export const isEmoteFx = (v: unknown): v is number =>
  typeof v === 'number' && Number.isInteger(v) && v >= 0 && v < EMOTE_FX_VARIANTS;

/** What an emote looks like on screen. A placeholder stands in for a custom emote whose files are missing. */
export type EmoteArt =
  | { kind: 'bundled'; slug: string }
  | { kind: 'custom'; url: string; animated: boolean; sound?: string; /** The signature effect it plays; 0 is the plain pop. */ fx: number }
  | { kind: 'placeholder' };

export type ImportError = 'type' | 'size' | 'animSize' | 'detail' | 'full' | 'duplicate' | 'disk';
/** What the settings renderer sends the main process when importing: the 160 px still and, optionally, the original animation. */
export interface EmoteFile {
  name: string;
  still: Uint8Array;
  anim?: Uint8Array;
}
export type ImportResult = { ok: true; id: EmoteRef } | { ok: false; error: ImportError };
export type SoundError = 'type' | 'size' | 'long' | 'disk';
export type SoundResult = { ok: true } | { ok: false; error: SoundError };

export const MAX_CUSTOM_EMOTES = 24;
export const EMOTE_DURATION_S = 3;
export const CUSTOM_NAME_MAX = 24;
export const STILL_MAX_BYTES = 12_288;
export const STILL_SIDE = 160;
export const STILL_FALLBACK_SIDE = 128;
export const INPUT_MAX_BYTES = 8_388_608;
/** The longest side of a picked image the settings window will decode: a small file can declare billions of pixels. */
export const INPUT_MAX_SIDE = 8192;
export const ANIM_MAX_BYTES = 5_242_880;
export const ANIM_MAX_SIDE = 1024;
/** An imported emote's sound: OGG, MP3 or WAV, at most this many bytes and seconds. */
export const SOUND_MAX_BYTES = 1_048_576;
export const SOUND_MAX_S = 5;
/**
 * The signature effects a custom emote can play when it lands: 0 is the plain pop, then spin-in, bounce drop, flash
 * focus, pendulum swing and heartbeat. Which one an image plays is stored on its entry (`CustomEmote.fx`, picked by
 * right-clicking it in the settings window); nothing stored plays the plain pop.
 */
export const EMOTE_FX_VARIANTS = 6;
