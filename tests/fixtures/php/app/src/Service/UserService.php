<?php

namespace App\Service;

require_once __DIR__ . '/../../bootstrap.php';
include 'helpers.php';

use App\Models\User;

interface Greeter
{
    public function greet(User $u): string;
}

trait Loggable
{
    public function log(string $m): void
    {
        // a closing brace } inside a comment must not end the method
        echo "a } brace and a # hash inside a string are not syntax";
    }
}

enum Status: string
{
    case Active = 'active';
}

class UserService implements Greeter
{
    use Loggable;

    public function greet(User $u): string
    {
        $sql = <<<SQL
            SELECT * FROM users;
            -- a heredoc containing } and function ghost() { must be inert
        SQL;
        return "Hello " . $u->getName();
    }
}

function make_service(): UserService
{
    return new UserService();
}
