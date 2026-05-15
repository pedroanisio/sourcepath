import 'package:test/test.dart';
import 'package:single_pkg/animals.dart';

void main() {
  test('Dog speaks', () {
    final d = Dog('Rex', age: 2);
    expect(d.speak(), contains('Rex'));
  });
}
