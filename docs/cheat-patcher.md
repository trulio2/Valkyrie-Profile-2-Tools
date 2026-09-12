# Cheat-patcher guide

The Cheats page in ValkyrieProfile2-Tools writes selected cheats directly into
a copy of the USA game image. The resulting ISO does not need a PCSX2 PNACH
file for those cheats to work. The source image is only read and is never
modified.

## Using the window

Open `ValkyrieProfile2-Tools` from a downloaded release, or run this
from a source checkout:

```bash
python vp2_tools.py
```

Select a clean USA image (`SLUS_214.52`), choose an output folder, select the
cheats, and choose **Patch ISO**. Only **Disable Anti-Cheat Systems** is
selected by default. The toggle above the list enables or disables all
cheats.

## Command line

Pass a source image to patch from the terminal. With no `--patch` option, all
available cheats are applied:

```bash
python vp2_cheats.py <usa-image.iso>
```

Use `--patch` to select individual cheats, and repeat it to select several:

```bash
python vp2_cheats.py <usa-image.iso> --patch angel-slayer
python vp2_cheats.py <usa-image.iso> \
  --patch angel-slayer \
  --patch equip-everything
```

Choose the output path with `--output`:

```bash
python vp2_cheats.py <usa-image.iso> --output <patched.iso> --patch angel-slayer
```

When running from source, the default output is
`build/<source-name>-cheat-patched.iso`. Run `python vp2_cheats.py --help` for
the complete arguments.

## Available cheats

| Command-line name                | Cheat                                                            | Effect                                                                 |
| -------------------------------- | ---------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `disable-anti-cheat`             | Disable Anti-Cheat Systems                                       | Stops the checksum that freezes the game after code changes.           |
| `battle-anti-freeze`             | (!) Anti Freeze In Battles                                       | Prevents the early-game freeze caused by using a late-game character.  |
| `stop-removing-characters`       | (!) Stop The Game From Removing Characters                       | Keeps protected party members when the active roster is rebuilt.       |
| `36-character-limit`             | (!) 36 Characters Limit                                          | Raises the permanent roster limit from 33 to 36.                       |
| `join-all-unlocked`              | (!) Characters Join With All Skills, Spells And Attacks Unlocked | Recruited characters arrive with every ability unlocked.               |
| `add-characters`                 | (!) Add Characters                                               | Holds L1+L2+R1+R2 to add the special-character roster to the party.    |
| `join-level-1`                   | (!) Characters Join At Level 1                                   | Makes the characters recruited by Add Characters join at level 1.      |
| `heavenly-punishment-15-ap`      | (!) Heavenly Punishment Costs 15 AP                              | Reduces Freya's Heavenly Punishment AP cost to 15.                     |
| `infinite-ap-attacks`            | (!) Infinite AP And Attacks                                      | Removes AP costs and the attack-chain limit in battle.                 |
| `battle-menu-always`             | (!) Battle Menu Always Available                                 | Removes the battle-menu cooldown.                                      |
| `dupe-attacks`                   | (!) Dupe Attacks                                                 | Allows the same attack in multiple attack slots.                       |
| `100-percent-drop-rate`          | (!) 100% Drop Rate                                               | Guarantees broken-part and boss drops.                                 |
| `negate-encounters`              | (!) Negate Encounters                                            | Permanently applies the Elusive Air Law effect.                        |
| `hold-circle-float`              | (!) Hold Circle To Float                                         | Lets the field character float after jumping while Circle is held.     |
| `equip-everything`               | Let Everyone Equip Everything                                    | Allows every character to equip every weapon and armor.                |
| `ether-set-effects`              | Ether Set Effects                                                | Gives passive effects to the four Ether-set pieces.                    |
| `angel-slayer`                   | Angel Slayer                                                     | Gives Angel Slayer three attacks.                                      |
| `restore-all-sealstones`         | Restore A Sealstone To Unlock All                                | Unlocks every sealstone when any one is restored.                      |
| `no-limit-sealstone-withdrawals` | No Limit For Sealstone Withdrawals                               | Removes the switching limit; its displayed value may become negative.  |
| `99-skill-points`                | 99 Skill Points                                                  | Gives every character 99 skill points in the Skill menu.               |
| `all-items-99`                   | All Items 99                                                     | Sets ordinary item stacks to 99 when the Items menu opens.             |

## Anti-cheat dependency

Cheats marked `(!)` change code checked by the game's own checksum. Without
**Disable Anti-Cheat Systems**, the game can freeze after events such as
cutscenes, battles, map changes, and saves. The window and command-line
launcher automatically add `disable-anti-cheat` whenever a selected cheat
requires it.

## Safety

The patcher supports only the USA release identified by `SLUS_214.52`. Before
writing the output, it checks the disc identity and verifies that every
selected patch fits. The completed output is read back before receiving its
final filename.
