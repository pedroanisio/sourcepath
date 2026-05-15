import 'package:core/greeter.dart';

void runApp() {
  final g = Greeter('Hi');
  print(g.hello('world'));
  print(greet('inline'));
}
