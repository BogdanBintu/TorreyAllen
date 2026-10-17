"""Dave XML/TCP and HAL encoder integration using a fake Tiger controller."""

import unittest
from unittest import mock
from xml.etree import ElementTree

from PyQt5 import QtWidgets

from storm_control.dave import daveActions
from storm_control.dave.xml_generators import nodeToDict, v2Generator
from storm_control.hal4000.film import filmRequest, filmSettings
from storm_control.hal4000.halLib import halMessage
from storm_control.hal4000.tcpControl import tcpControl
from storm_control.sc_hardware.appliedScientificInstrumentation import tigerModule
from storm_control.sc_hardware.appliedScientificInstrumentation.tigerEncoderScan import EncoderScanSettings
from storm_control.sc_library import parameters, tcpMessage
from storm_control.test.test_tiger_encoder_scan import FakeTiger


class DaveEncoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def movie_message(self, option=""):
        movie = ElementTree.fromstring(
            "<movie><name>fov</name><length>40</length>" + option + "</movie>")
        action = daveActions.DATakeMovie()
        action.setup(action.createETree(nodeToDict.movieNodeToDict(movie)))
        # Exercise the actual TCP serialization, including Boolean type.
        return tcpMessage.TCPMessage.fromJSON(action.message.toJSON())

    def test_recipe_flag_survives_actions_and_tcp(self):
        for text, expected in (("True", True), ("False", False), (" false ", False)):
            with self.subTest(text=text):
                message = self.movie_message("<reuse_encoder_scan>" + text + "</reuse_encoder_scan>")
                request = tcpControl.TCPActionTakeMovie(tcp_message=message).film_request
                self.assertIs(request.reuseEncoderScan(), expected)

    def test_old_recipes_and_manual_films_default_to_full_setup(self):
        request = tcpControl.TCPActionTakeMovie(tcp_message=self.movie_message()).film_request
        self.assertIs(request.reuseEncoderScan(), False)
        self.assertIs(filmRequest.FilmRequest().reuseEncoderScan(), False)
        self.assertIs(filmSettings.FilmSettings().reuseEncoderScan(), False)

    def test_invalid_option_is_rejected_instead_of_enabling_reuse(self):
        for value in ("yes", "1", "", "flase"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.movie_message("<reuse_encoder_scan>" + value + "</reuse_encoder_scan>")

    def test_primitive_xml_flag(self):
        node = ElementTree.fromstring(
            "<DATakeMovie><name>fov</name><length>40</length>"
            "<reuse_encoder_scan type='bool'>True</reuse_encoder_scan></DATakeMovie>")
        action = daveActions.DATakeMovie()
        action.setup(node)
        self.assertIs(action.message.getData("reuse_encoder_scan"), True)

    def test_recipe_preserves_stage_move_before_acquisition(self):
        recipe = ElementTree.fromstring(
            "<command_sequence><movie><name>fov</name><length>40</length>"
            "<stage_x>100</stage_x><stage_y>200</stage_y>"
            "<reuse_encoder_scan>True</reuse_encoder_scan></movie></command_sequence>")
        parser = v2Generator.XMLRecipeParser(verbose=False)
        primitive = ElementTree.Element("sequence")
        parser.convertToDaveXMLPrimitives(primitive, recipe)
        branch = primitive.find("branch")
        self.assertEqual([node.tag for node in branch], ["DAMoveStage", "DATakeMovie"])
        self.assertEqual(branch.findtext("DAMoveStage/stage_z"), "-1.0")
        self.assertEqual(branch.findtext("DATakeMovie/reuse_encoder_scan"), "True")

    def make_controller(self):
        module_params = parameters.StormXMLObject()
        config = module_params.addSubSection("configuration")
        config.add(parameters.ParameterSetBoolean(name="encoder_scan_supported", value=True))
        config.add(parameters.ParameterInt(name="baudrate", value=115200))
        config.add(parameters.ParameterString(name="port", value="test"))
        config.addSubSection("devices")
        fake = FakeTiger()
        with mock.patch.object(tigerModule.tiger, "Tiger", return_value=fake):
            controller = tigerModule.TigerController(
                module_name="tiger_controller", module_params=module_params)
        controller.encoder_scan.apply_settings(EncoderScanSettings(enabled=True))
        controller.encoder_parameters.setv("encoder_scan.enabled", True)
        return controller, fake

    def test_reuse_is_per_movie_and_only_starts_at_master_phase(self):
        controller, fake = self.make_controller()
        # Execute hardware tasks synchronously; never open a serial connection.
        with mock.patch.object(tigerModule.hardwareModule, "runHardwareTask",
                               side_effect=lambda module, message, task: task()):
            for reuse, prepares in ((False, True), (True, False), (False, True)):
                message = self.movie_message(
                    "<reuse_encoder_scan>" + str(reuse) + "</reuse_encoder_scan>")
                request = tcpControl.TCPActionTakeMovie(tcp_message=message).film_request
                settings = filmSettings.FilmSettings(reuse_encoder_scan=request.reuseEncoderScan())
                controller.startFilm(halMessage.HalMessage(
                    m_type="start film", data={"film settings": settings}))
                fake.commands.clear()
                controller.processEncoderMessage(halMessage.HalMessage(
                    m_type="start camera", data={"master": False}))
                self.assertEqual(fake.commands, [])
                controller.processEncoderMessage(halMessage.HalMessage(
                    m_type="start camera", data={"master": True}))
                self.assertEqual("W Z" in fake.commands, prepares)
                self.assertEqual(fake.commands.count("2SCAN"), 1)
                controller.processEncoderMessage(halMessage.HalMessage(
                    m_type="start camera", data={"master": True}))
                self.assertEqual(fake.commands.count("2SCAN"), 1)
                controller.processEncoderMessage(halMessage.HalMessage(
                    m_type="stop camera", data={"master": True}))
                controller.stopFilm(halMessage.HalMessage(
                    m_type="stop film", data={"film settings": settings}))
                self.assertFalse(controller.encoder_reuse_setup)


if __name__ == "__main__":
    unittest.main()
