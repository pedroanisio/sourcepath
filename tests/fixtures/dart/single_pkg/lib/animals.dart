/// Animals library — exercises classes, mixins, getters, setters, methods.
library animals;

import 'dart:math';
import 'package:collection/collection.dart' show ListEquality;

import 'sounds.dart';

abstract class Animal {
  String get name;
  String speak();
}

mixin Eats {
  void eat(String food) {
    // Body intentionally uses bark() and Sound() so call edges resolve.
    final s = Sound('chomp');
    s.amplify();
  }
}

class Dog extends Animal with Eats {
  final String _name;
  int age;

  Dog(this._name, {this.age = 0});

  factory Dog.puppy(String name) => Dog(name, age: 0);

  @override
  String get name => _name;

  set rename(String value) {
    // setter exercising body chunk.
    print(value);
  }

  @override
  String speak() {
    final s = bark();
    return '$_name says ${s.text}';
  }
}

Sound bark() => Sound('woof');

int randomBetween(int lo, int hi) {
  final r = Random();
  return lo + r.nextInt(hi - lo);
}
