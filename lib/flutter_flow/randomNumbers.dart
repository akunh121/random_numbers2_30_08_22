import 'dart:ffi';
import 'dart:math' as math;

List<int> array = List.empty(growable: true);
bool ok = false;

List<int> randomNumbers1() {
  if (ok == true) {
    array = List.empty(growable: true);
    ok = false;
  }
  while (array.length != 6) {
    var random1 = math.Random();
    int num1 = random1.nextInt(2147483647) + 1;
    // int num2=random1.hashCode();
    var random = math.Random(num1);
    int num = random.nextInt(37) + 1;
    while (array.contains(num)) {
      num = random.nextInt(37) + 1;
    }
    array.add(num);
    randomNumbers1();
  }
  array.sort((b, a) => a.compareTo(b));
  ok = true;
  return array;
}
//how to connect firebase to dart function?

