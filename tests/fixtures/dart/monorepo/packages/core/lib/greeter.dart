String greet(String name) => 'Hello, $name';

class Greeter {
  final String prefix;
  Greeter(this.prefix);

  String hello(String name) => '$prefix, $name';
}
