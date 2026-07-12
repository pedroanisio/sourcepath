<?php
declare(strict_types=1);

namespace App\Models;

/**
 * Inheritance fixture (BL-037).
 *
 * Encodes the PHP-specific hazards a `bases` extractor must survive:
 *   - extends and implements on one declaration, implements taking a list
 *   - an interface extending SEVERAL parents (PHP allows it; a class cannot)
 *   - a BACKED enum: `enum Status: string implements X` — the `: string` is a
 *     backing type, not a base, and must never be captured as one
 *   - a comment sitting INSIDE the header, between the name and the brace
 *   - a string in a body that spells a whole fake class declaration
 *   - `use T;` inside a class body: trait composition, not inheritance
 *   - a fully-qualified base name
 *   - a class with no clause at all: it must carry no `bases` key
 */

interface Identifiable extends Countable, Stringable
{
    public function id(): int;
}

trait Timestamped
{
    public function touchedAt(): int
    {
        return 0;
    }
}

enum Status: string implements Identifiable
{
    case Active = 'active';
    case Banned = 'banned';

    public function id(): int
    {
        return 1;
    }

    public function count(): int
    {
        return 2;
    }
}

final class Admin
    extends User /* extends GhostBase implements GhostFace */
    implements Identifiable, \App\Contracts\Auditable
{
    use Timestamped;

    public function id(): int
    {
        $sql = "class Phantom extends Spectre implements Wraith {";
        return 7;
    }
}

class Bare
{
}
